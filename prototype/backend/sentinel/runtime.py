"""把所有模組接起來（main.py 的 lifespan 與測試共用）。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from .agent.repair_agent import AgentContext, RepairAgent
from .audit.log import AuditLogger
from .bus import EventBus
from .clock import Clock, ManualClock
from .config import Settings, load_settings
from .db.seed import seed_all
from .db.session import Database
from .dispatch.notifier import Notifier, make_notifier
from .dispatch.reports import ReportService
from .dispatch.service import DispatchService
from .dispatch.workorder import WorkorderService
from .jev.client import JevClient
from .kb.retrieve import KnowledgeBase
from .kb.store import VectorStore, get_embedder
from .kb.writeback import CaseWriteback
from .llm.client import LlmClient
from .rules.engine import IncidentManager, RuleEngine, load_rules
from .rules.escalation import EscalationService
from .rules.windows import Windows
from .schemas.events import Event, Topic
from .schemas.plant import Plant
from .sim.camera import VirtualCamera
from .sim.engine import SimEngine
from .sim.mes import MesSimulator
from .sim.roster import Roster
from .sim.scenario import Scenario, load_scenarios
from .vision.ppe import PpeDetector, YoloPpeDetector
from .vision.service import VisionService

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings, *, clock: Clock | None = None, notifier: Notifier | None = None,
                 jev_transport: httpx.AsyncBaseTransport | None = None,
                 llm_transport: httpx.AsyncBaseTransport | None = None,
                 jev_key: str | None = None, llm_key: str | None = None, use_vector_store: bool = True):
        self.settings = s = settings
        self.clock = clock or Clock()
        self.db = Database(s.path("db"))
        self.db.create_all()
        self.bus = EventBus(persist=self.db.persist_event)
        self.audit = AuditLogger(self.db, self.clock)
        self.plant = Plant.load(s.path("plant"))
        self.seed_counts = seed_all(self.db, self.plant, s.path("roster"), s.path("access"))
        self.roster = Roster(self.db)
        self.scenarios: dict[str, Scenario] = load_scenarios(s.path("scenarios"))

        jev_key = jev_key if jev_key is not None else s.secret(s.secrets.openrouter_api_key)
        llm_key = llm_key if llm_key is not None else s.secret(s.secrets.ollama_api_key)
        self.jev = JevClient(api_key=jev_key, url=s.jev.url, model=s.jev.model, timeout_s=s.jev.timeout_s, db=self.db,
                             clock=self.clock, enabled=s.jev.enabled, transport=jev_transport)
        self.llm = LlmClient(api_key=llm_key, url=s.llm.url, model=s.llm.model, timeout_s=s.llm.timeout_s, db=self.db,
                             clock=self.clock, enabled=s.llm.enabled, transport=llm_transport)

        store = None
        if use_vector_store:
            try:
                store = VectorStore(s.path("chroma"), get_embedder(s.kb.embedding, s.kb.embedding_model),
                                    index_text=s.kb.index_text)
            except Exception:
                log.exception("Chroma 無法啟動，案例檢索停用")
        self.kb = KnowledgeBase(s.path("knowledge"), store, allow_draft=s.kb.allow_draft, retrieval=s.kb.retrieval)
        self.writeback = CaseWriteback(self.kb, s.path("drafts"))

        self.windows = Windows(self.clock, s.thresholds, plant=self.plant)
        self.rules = RuleEngine(self.plant, self.windows, load_rules())
        self.incidents = IncidentManager(db=self.db, bus=self.bus, clock=self.clock, audit=self.audit,
                                         plant=self.plant, thresholds=s.thresholds)
        self.notifier = notifier or make_notifier(s.secret(s.secrets.discord_bot_token), s.discord.channels)
        self.workorders = WorkorderService(db=self.db, bus=self.bus, clock=self.clock, audit=self.audit,
                                           require_permit=s.workflow.require_permit)
        self.dispatch = DispatchService(db=self.db, bus=self.bus, clock=self.clock, audit=self.audit, settings=s,
                                        plant=self.plant, roster=self.roster, kb=self.kb, jev=self.jev, llm=self.llm,
                                        notifier=self.notifier, workorders=self.workorders, windows=self.windows)
        self.escalation = EscalationService(db=self.db, clock=self.clock, settings=s, roster=self.roster,
                                            workorders=self.workorders, dispatch=self.dispatch)
        self.agent = RepairAgent(AgentContext(db=self.db, bus=self.bus, clock=self.clock, audit=self.audit, kb=self.kb,
                                              llm=self.llm, jev=self.jev, windows=self.windows, plant=self.plant,
                                              workorders=self.workorders, dispatch=self.dispatch,
                                              thresholds=s.thresholds, require_permit=s.workflow.require_permit))

        videos_root = s.path("videos_root")
        self.cameras = {c.id: VirtualCamera(c, self.bus, self.clock, videos_root, fps=s.vision.fps)
                        for c in self.plant.cameras.values()}
        self.mes = MesSimulator(self.plant, self.bus, self.clock, seed=s.mes.seed)
        self.vision = VisionService(bus=self.bus, clock=self.clock, plant=self.plant, settings=s, jev=self.jev,
                                    cameras=self.cameras, ppe_detector=None)
        self.reports = ReportService(self)
        self.engine = SimEngine(self)
        self.incidents.on_opened = self.save_keyframe
        self._bg: list[asyncio.Task] = []
        self.started = False

    @classmethod
    def from_config(cls, config_path: str | Path | None = None, **kw) -> "Runtime":
        return cls(load_settings(config_path), **kw)

    # ── 偵測器 ─────────────────────────────────────────────────
    def make_ppe_detector(self, sc: Scenario | None) -> PpeDetector | None:
        """有 models/ppe.pt 且 PPE 攝影機播的是真實影片 → YOLO；合成畫面（影片還沒備齊）→ 劇本重播的偵測結果。"""
        weights = self.settings.base_dir / self.settings.vision.yolo_weights
        ppe_cams = [c for c in self.cameras.values() if "ppe" in c.cam.detectors]
        real_video = any(c.synthetic is None and c.online for c in ppe_cams)
        if YoloPpeDetector.available(weights) and (real_video or not (sc and sc.sim_detections)):
            if not isinstance(self.vision.ppe_detector, YoloPpeDetector):
                return YoloPpeDetector(weights, self.settings.vision.yolo_device, self.settings.vision.yolo_classes)
            return self.vision.ppe_detector
        return SimEngine.scripted_detector(sc)

    def keyframe_path(self, incident_id: str) -> Path:
        return self.settings.path("keyframes") / f"{incident_id}.jpg"

    def save_keyframe(self, inc) -> None:
        """異常成立時存一張關鍵幀；有人員的畫面（PPE 攝影機）先模糊臉部（SE-6）。"""
        cam = self.cameras.get(inc.camera_id or "")
        frame = cam.latest_frame() if cam else None
        if frame is None:
            return
        import cv2

        from .vision.face_blur import blur_faces

        if "ppe" in cam.cam.detectors:
            frame, _ = blur_faces(frame, fallback_head=False)
        path = self.keyframe_path(inc.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

    # ── 生命週期 ───────────────────────────────────────────────
    async def start(self, *, realtime: bool | None = None, warmup: bool | None = None) -> None:
        """啟動消費者；realtime=True 時同時啟動模擬播放迴圈。"""
        if self.started:
            return
        self.started = True
        self.bus.consume(Topic.VISION, "rules.vision", self.on_sensor)
        self.bus.consume(Topic.MES, "rules.mes", self.on_sensor)
        self.vision.start()
        self.dispatch.start()
        if warmup if warmup is not None else (self.settings.jev.warmup and self.jev.enabled):
            self._bg.append(asyncio.create_task(self.jev.warmup(), name="jev.warmup"))
            if self.llm.enabled:                     # 維修 Agent 首字延遲：先建好 Ollama Cloud 連線
                self._bg.append(asyncio.create_task(self.llm.warmup(), name="llm.warmup"))
        if realtime if realtime is not None else self.clock.realtime:
            self.engine.start_realtime(None)
        log.info("sentinel-core 啟動：%s；Jev=%s LLM=%s 通知=%s", self.seed_counts,
                 "on" if self.jev.enabled else "off", "on" if self.llm.enabled else "off", self.notifier.name)

    async def on_sensor(self, evt: Event) -> None:
        entity = self.windows.ingest(evt)
        if not entity or evt.type in ("camera_offline",):
            return
        for spec, finding in self.rules.evaluate(entity):
            await self.incidents.on_finding(spec, finding, entity)

    async def run_scenario(self, scenario_id: str) -> Scenario:
        sc = self.scenarios[scenario_id]
        if self.clock.realtime:
            self.engine.start_realtime(sc)
        else:
            await self.engine.run_stepped(sc)
        return sc

    async def reset(self) -> None:
        """重置 Demo 資料：清空資料庫並重新載入初始資料，回到背景模式。"""
        realtime = self.engine._task is not None
        await self.engine.stop()
        await self.bus.drain(timeout=10)
        self.db.reset()
        self.seed_counts = seed_all(self.db, self.plant, self.settings.path("roster"), self.settings.path("access"))
        self.audit.record("demo_reset")
        if realtime:
            self.engine.start_realtime(None)
        else:
            self.engine.load(None)

    async def stop(self) -> None:
        await self.engine.stop()
        for t in self._bg:
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        await self.bus.close()
        await self.jev.aclose()
        await self.llm.aclose()


def manual_clock(settings: Settings) -> ManualClock:
    return ManualClock(settings.app.sim_start)
