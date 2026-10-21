"""影像感測服務：訂閱 frames，依 plant.yaml 每支攝影機的 detectors 執行偵測，結果發布到 vision.events。

| 偵測器 | 頻率 | 輸出 |
|---|---|---|
| motion | 每幀 | machine_state（狀態改變時才發） |
| ppe | ppe_fps | ppe_violation（同一人 3 秒內不重複） |
| jev_feed | 每 N 秒＋Log 異常時 | jev_vision {p_jam, p_smoke, box} |
| jev_smoke | 每 N 秒＋機器人停止時 | smoke {value: none／outside_cell, p_smoke_outside, box}；flame |
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from ..bus import EventBus
from ..clock import Clock
from ..config import Settings
from ..jev import vision as jev_vision
from ..jev.client import JevClient, JevUnavailable
from ..schemas.events import Event, Topic
from ..schemas.plant import Camera, Plant
from .face_blur import blur_faces
from .keyframe import KeyframeScheduler
from .motion import MotionDetector
from .ppe import PpeDetector, ViolationTracker, find_violations

if TYPE_CHECKING:
    from ..sim.camera import VirtualCamera

log = logging.getLogger(__name__)

JEV_DETECTORS = {"jev_feed", "jev_smoke"}     # 一支攝影機最多一種 Jev 看圖偵測器


class VisionService:
    def __init__(self, *, bus: EventBus, clock: Clock, plant: Plant, settings: Settings, jev: JevClient,
                 cameras: dict[str, "VirtualCamera"], ppe_detector: PpeDetector | None = None):
        self.bus, self.clock, self.plant, self.settings, self.jev = bus, clock, plant, settings, jev
        self.cameras = cameras
        self.ppe_detector = ppe_detector
        self.th = settings.thresholds
        self.keyframes = KeyframeScheduler(settings.vision.keyframe_interval_s)
        self.motion: dict[str, MotionDetector] = {}
        self.trackers: dict[str, ViolationTracker] = {}
        self._last_ppe_t: dict[str, float] = {}
        self._jev_busy: set[str] = set()
        self._feed_prev: dict[str, float] = {}
        self._last_demand: dict[str, float] = {}
        self._robot_prev: dict[str, str] = {}
        self.overlays: dict[str, dict] = {}       # 給 MJPEG 畫框
        self.scenario_t0: float = 0.0             # 劇本開始時的 clock.monotonic()
        self.reset()

    def reset(self) -> None:
        self.keyframes.reset()
        self.motion = {c.id: MotionDetector(c.rois.get(c.motion.roi), c.motion)
                       for c in self.plant.cameras.values() if "motion" in c.detectors}
        self.trackers = {c.id: ViolationTracker(self.th.ppe_dedupe_s) for c in self.plant.cameras.values()}
        self._last_ppe_t.clear()
        self._feed_prev.clear()
        self._last_demand.clear()
        self._robot_prev.clear()
        self.overlays.clear()
        self.scenario_t0 = self.clock.monotonic()

    def start(self) -> None:
        self.bus.consume(Topic.FRAMES, "vision", self.on_frame, maxsize=30)
        self.bus.consume(Topic.MES, "vision.trigger", self.on_mes)

    @property
    def t(self) -> float:
        return self.clock.monotonic() - self.scenario_t0

    # ── 幀處理 ─────────────────────────────────────────────────
    async def on_frame(self, evt: Event) -> None:
        cam = self.plant.cameras.get(evt.camera_id or "")
        frame = evt.payload.get("frame")
        if cam is None or not isinstance(frame, np.ndarray):
            return
        if "motion" in cam.detectors:
            await self._motion(cam, frame)
        if "ppe" in cam.detectors and self.ppe_detector is not None:
            await self._ppe(cam, frame)
        if JEV_DETECTORS & set(cam.detectors):
            reason = self.keyframes.due(cam.id, self.t)
            if reason:
                self._spawn_feed(cam, frame, reason)

    async def _motion(self, cam: Camera, frame: np.ndarray) -> None:
        det = self.motion[cam.id]
        state, score, changed = det.update(frame)
        self.overlays.setdefault(cam.id, {})["motion"] = {"state": state, "score": round(score, 3)}
        if changed and state:
            await self._publish(cam, "vision.motion", "machine_state", {"state": state, "motion_score": round(score, 3)})

    async def _ppe(self, cam: Camera, frame: np.ndarray) -> None:
        t = self.t
        period = 1.0 / max(self.settings.vision.ppe_fps, 0.1)
        if t - self._last_ppe_t.get(cam.id, -1e9) < period - 1e-6:
            return
        self._last_ppe_t[cam.id] = t
        dets = self.ppe_detector.detect(frame, t)  # type: ignore[union-attr]
        violations = find_violations(dets, self.th.ppe_overlap, self.th.ppe_conf)
        self.overlays.setdefault(cam.id, {})["ppe"] = [
            {"box": list(map(round, v.person_box)), "label": "未戴安全帽"} for v in violations]
        for v in violations:
            if not self.trackers[cam.id].should_emit(v, t):
                continue
            payload = {"person_box": [round(x) for x in v.person_box], "head_box": [round(x) for x in v.head_box],
                       "missing": v.missing, "conf": round(v.conf, 3), "overlap": round(v.overlap, 3),
                       "detector": self.ppe_detector.name}  # type: ignore[union-attr]
            if self.settings.vision.ppe_jev_confirm:
                self.bus.track(self._confirm_ppe(cam, frame, payload), name=f"jev.ppe:{cam.id}")
            else:
                await self._publish(cam, "vision.ppe", "ppe_violation", payload)

    async def _confirm_ppe(self, cam: Camera, frame: np.ndarray, payload: dict) -> None:
        """V1（M2）：人員裁切圖先模糊臉部再送 Jev；Jev 不可用時照 YOLO 結果發布。"""
        x1, y1, x2, y2 = payload["person_box"]
        h, w = frame.shape[:2]
        crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if crop.size:
            blurred, _ = blur_faces(crop)
            try:
                j = await jev_vision.ppe(self.jev, blurred)
                payload |= {"jev_p_no_helmet": j.p_no_helmet, "jev_p_in_zone": j.p_in_zone}
                if j.p_no_helmet < 0.3:
                    return
            except JevUnavailable:
                payload["jev"] = "unavailable"
        await self._publish(cam, "vision.ppe", "ppe_violation", payload)

    # ── Jev 看圖（V2）──────────────────────────────────────────
    def _spawn_feed(self, cam: Camera, frame: np.ndarray, reason: str) -> None:
        if cam.id in self._jev_busy and not reason.startswith("on_demand"):
            return                                           # 上一張還沒回來，定時那張跳過
        self._jev_busy.add(cam.id)
        fn = self._smoke if "jev_smoke" in cam.detectors else self._feed
        self.bus.track(fn(cam, frame.copy(), reason), name=f"jev.vision:{cam.id}")

    async def _smoke(self, cam: Camera, frame: np.ndarray, reason: str) -> None:
        machine = self.plant.machines.get(cam.machine_id or "")
        try:
            j = await jev_vision.smoke(self.jev, frame, cam.rois.get("weld_cell"),
                                       machine.name if machine else (cam.machine_id or cam.id),
                                       p_thr=self.th.p_smoke_outside, max_side=self.settings.vision.jev_max_side,
                                       quality=self.settings.vision.jpeg_quality)
        except JevUnavailable as e:
            self.overlays.setdefault(cam.id, {})["jev"] = {"error": str(e)}
            return
        finally:
            self._jev_busy.discard(cam.id)
        payload = {"value": j.value, "p_smoke_outside": round(j.p_smoke_outside, 4), "p_flame": round(j.p_flame, 4),
                   "box": j.box, "trigger": reason, "latency_ms": j.latency_ms, "cost": j.cost}
        self.overlays.setdefault(cam.id, {})["jev"] = payload
        await self._publish(cam, "vision.jev_smoke", "smoke", payload)
        if j.p_flame > self.th.p_flame:
            await self._publish(cam, "vision.jev_smoke", "flame", {"value": True, "p_flame": round(j.p_flame, 4)})

    async def _feed(self, cam: Camera, frame: np.ndarray, reason: str) -> None:
        roi = cam.rois.get("feed_inlet")
        machine = self.plant.machines.get(cam.machine_id or "")
        try:
            j = await jev_vision.feed(self.jev, frame, roi, machine.name if machine else (cam.machine_id or cam.id),
                                      p_jam_box=self.th.p_jam, max_side=self.settings.vision.jev_max_side,
                                      quality=self.settings.vision.jpeg_quality)
        except JevUnavailable as e:
            self.overlays.setdefault(cam.id, {})["jev"] = {"error": str(e)}
            log.debug("Jev 看圖不可用（%s）：%s", cam.id, e)
            return
        finally:
            self._jev_busy.discard(cam.id)
        payload = {"p_jam": round(j.p_jam, 4), "p_smoke": round(j.p_smoke, 4), "box": j.box,
                   "box_conf": j.box_conf, "trigger": reason, "latency_ms": j.latency_ms, "cost": j.cost}
        self.overlays.setdefault(cam.id, {})["jev"] = payload
        await self._publish(cam, "vision.jev_feed", "jev_vision", payload)

    async def on_mes(self, evt: Event) -> None:
        """Log 出現異常徵兆（異常碼、送料速率歸零）→ 對看這台機器的攝影機加送關鍵幀。"""
        mid = evt.machine_id or ""
        reason = None
        if evt.type == "machine_alarm":
            reason = f"alarm:{evt.payload.get('alarm_code')}"
        elif evt.type == "mes_log" and "robot_state" in evt.payload:      # 機器人停止 → 看工位外有沒有冒煙
            rs, prev_rs = evt.payload.get("robot_state"), self._robot_prev.get(mid)
            self._robot_prev[mid] = rs
            if prev_rs == "running" and rs != "running":
                reason = f"robot_{rs}"
        elif evt.type == "mes_log":
            fr = evt.payload.get("feed_rate")
            prev = self._feed_prev.get(mid)
            if isinstance(fr, (int, float)):
                if prev is not None and prev > 0 and fr <= 0:
                    reason = "feed_rate_zero"
                self._feed_prev[mid] = fr
        if not reason:
            return
        for cam in self.plant.cameras_for_machine(mid):
            if not JEV_DETECTORS & set(cam.detectors):
                continue
            if self.t - self._last_demand.get(cam.id, -1e9) < 1.0:   # 同一秒內多個觸發只送一張
                continue
            self._last_demand[cam.id] = self.t
            frame = self.cameras[cam.id].latest_frame() if cam.id in self.cameras else None
            if frame is not None:
                self.keyframes.due(cam.id, self.t)           # 重設定時器
                self._spawn_feed(cam, frame, f"on_demand:{reason}")
            else:
                self.keyframes.request(cam.id, reason)

    async def _publish(self, cam: Camera, source: str, type_: str, payload: dict) -> None:
        await self.bus.publish(Event(ts=self.clock.now(), topic=Topic.VISION, source=source, line=cam.line,
                                     machine_id=cam.machine_id, camera_id=cam.id, type=type_, payload=payload))
