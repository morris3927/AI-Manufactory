"""模擬引擎：同一個 tick() 給「即時播放」與「逐步重播（測試）」共用。

每個 tick（預設 1 秒模擬時間）：MES 每台機器一筆 → 每支攝影機送出 fps×dt 幀 → 檢查升級計時。
逐步模式在每個 tick 後呼叫 bus.drain()，結果完全可重現。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..schemas.events import Event, Topic
from ..vision.ppe import Detection, ScriptedPpeDetector
from .scenario import Scenario

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)


class SimEngine:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.t = 0.0                       # 劇本開始後的秒數
        self.scenario: Scenario | None = None
        self.scenario_started_at = None
        self._task: asyncio.Task | None = None
        self._frame_acc: dict[str, float] = {}
        self._events_done = 0
        self._casualty_done = False

    # ── 劇本 ───────────────────────────────────────────────────
    def load(self, sc: Scenario | None) -> None:
        rt = self.rt
        self.scenario = sc
        self.t = 0.0
        self._frame_acc.clear()
        self._events_done = 0
        self._casualty_done = False
        rt.windows.clear()
        rt.vision.reset()
        rt.mes.load_script(sc.mes_script if sc else {})
        for cam_id, cam in rt.cameras.items():
            cue = sc.cameras.get(cam_id) if sc else None
            if cue:
                cam.set_source(cue.video, then=cue.then, synthetic=cue.synthetic, loop=cue.loop,
                               jam_at_s=cue.event_at_s if cue.event_at_s is not None else cue.jam_at_s)
            else:
                cam.set_source(cam.cam.default_video, loop=True)
            rt.bus.track(rt.bus.publish(Event(
                ts=rt.clock.now(), topic=Topic.VISION, source="sim.camera", line=cam.cam.line,
                machine_id=cam.cam.machine_id, camera_id=cam_id, type="camera_source",
                payload={"video": cam.video, "synthetic": cam.synthetic})))
        rt.vision.ppe_detector = rt.make_ppe_detector(sc)
        self.scenario_started_at = rt.clock.now()
        if sc:
            rt.audit.record("scenario_started", scenario_id=sc.id, title=sc.title)
            log.info("劇本開始：%s", sc.title)
            rt.bus.track(rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.MES, source="sim.scenario",
                                              type="scenario_started", payload={"scenario_id": sc.id, "title": sc.title})))

    async def _scripted_casualty(self) -> None:
        """劇本設定的傷亡（模擬「主管在介面標記」；系統不從影像推論傷亡）。"""
        sc = self.scenario
        c = sc.casualty if sc else None
        if not c or self._casualty_done or self.t < c.t:
            return
        from sqlmodel import select

        from ..db.models import Incident

        ent = c.machine_id or c.camera_id
        inc = self.rt.db.first(select(Incident).where(
            (Incident.machine_id == ent) | (Incident.camera_id == ent), Incident.status == "open"))
        if inc is None:
            return
        self._casualty_done = True
        await self.rt.reports.set_casualty(inc.id, deaths=c.deaths, injured=c.injured, hospitalized=c.hospitalized,
                                           actor=c.set_by, note="劇本腳本（模擬主管標記）")

    async def _scripted_events(self) -> None:
        sc = self.scenario
        if not sc:
            return
        evs = sorted(sc.events, key=lambda e: e.t)
        while self._events_done < len(evs) and evs[self._events_done].t <= self.t:
            e = evs[self._events_done]
            self._events_done += 1
            cam = self.rt.plant.cameras.get(e.camera_id or "")
            mid = e.machine_id or (cam.machine_id if cam else None)
            await self.rt.bus.publish(Event(ts=self.rt.clock.now(), topic=e.topic, source="sim.scenario",
                                            line=self.rt.plant.line_of(mid or e.camera_id), machine_id=mid,
                                            camera_id=e.camera_id, type=e.type, payload=e.payload))

    @staticmethod
    def scripted_detector(sc: Scenario | None) -> ScriptedPpeDetector | None:
        if not sc or not sc.sim_detections:
            return None
        script = [(d.t_from, d.t_to, [Detection(x["cls"], tuple(x["box"]), float(x.get("conf", 0.9)))
                                      for x in d.detections]) for d in sc.sim_detections]
        return ScriptedPpeDetector(script=script)

    # ── tick ───────────────────────────────────────────────────
    async def tick(self, dt: float = 1.0, fps: float | None = None) -> None:
        rt = self.rt
        await rt.mes.tick(self.t)
        await self._scripted_events()
        for cam_id, cam in rt.cameras.items():
            f = fps or cam.fps
            self._frame_acc[cam_id] = self._frame_acc.get(cam_id, 0.0) + dt * f
            n = int(self._frame_acc[cam_id] + 1e-9)
            self._frame_acc[cam_id] -= n
            for _ in range(n):
                if not rt.clock.realtime:
                    await rt.bus.drain()
                await cam.emit()
        await rt.escalation.check()
        await rt.reports.tick()
        await self._scripted_casualty()

    async def run_stepped(self, sc: Scenario | None, duration_s: float | None = None, *, dt: float = 1.0,
                          fps: float = 2.0) -> dict[str, Any]:
        """逐步重播（ManualClock）：每一步推進時鐘、tick、等匯流處理完。"""
        clock = self.rt.clock
        assert not clock.realtime, "逐步重播需要 ManualClock"
        for cam in self.rt.cameras.values():
            cam.fps = fps
        self.load(sc)
        total = duration_s if duration_s is not None else (sc.duration_s if sc else 10)
        steps = int(round(total / dt))
        for _ in range(steps + 1):
            await self.tick(dt, fps)
            await self.rt.bus.drain()
            clock.advance(dt)                          # type: ignore[attr-defined]
            self.t += dt
        return {"scenario": sc.id if sc else None, "t": self.t}

    # ── 即時 ───────────────────────────────────────────────────
    def start_realtime(self, sc: Scenario | None = None) -> None:
        self.load(sc)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="sim.engine")

    async def _loop(self) -> None:
        tick_s = self.rt.settings.mes.tick_s
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        while True:
            fps = max(c.fps for c in self.rt.cameras.values()) if self.rt.cameras else 1
            sub = max(1, int(fps * tick_s))           # 把一個 tick 拆成多段，讓幀平均分佈
            for i in range(sub):
                if i == 0:
                    await self.rt.mes.tick(self.t)
                    await self._scripted_events()
                    await self.rt.escalation.check()
                    await self.rt.reports.tick()
                    await self._scripted_casualty()
                for cam in self.rt.cameras.values():
                    await cam.emit()
                nxt += tick_s / sub
                await asyncio.sleep(max(0.0, nxt - loop.time()))
            self.t += tick_s
            if self.scenario and self.t > self.scenario.duration_s + 60 and self.rt.settings.app.ambient:
                log.info("劇本 %s 結束，回到背景模式", self.scenario.id)
                self.scenario = None

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
