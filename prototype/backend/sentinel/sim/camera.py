"""攝影機模擬器（IN-1）。

VirtualCamera 用 OpenCV 讀 mp4；影片不存在時改用合成畫面（方便在素材備齊前跑通整條流程）。
介面與之後接 RTSP 的 RtspCamera 相同（抽象類別 CameraSource）。
"""
from __future__ import annotations

import asyncio
import logging
import math
from abc import ABC, abstractmethod
from pathlib import Path

import cv2
import numpy as np

from ..bus import EventBus
from ..clock import Clock
from ..schemas.events import Event, Topic
from ..schemas.plant import Camera

log = logging.getLogger(__name__)

FRAME_W, FRAME_H = 1280, 720


class CameraSource(ABC):
    camera_id: str
    fps: float = 15
    online: bool = True

    @abstractmethod
    async def play(self, video_path: str, loop: bool = False, fps: float = 15) -> None: ...

    @abstractmethod
    async def switch(self, video_path: str) -> None: ...

    @abstractmethod
    def latest_frame(self) -> np.ndarray | None: ...

    @abstractmethod
    async def emit(self) -> None:
        """讀下一幀並發布到 frames（播放迴圈與逐步重播都呼叫這個）。"""


# ── 合成畫面 ────────────────────────────────────────────────────
def synthetic_frame(pattern: str, t: float, cam: Camera | None = None, jam_at_s: float = 10.0) -> np.ndarray:
    """產生 1280×720 的合成畫面。pattern：normal／feed_jam／idle／workers／ppe／weld_cell／weld_smoke／station／offline。
    jam_at_s：卡料或冒煙開始的影片秒數。"""
    img = np.full((FRAME_H, FRAME_W, 3), 48, np.uint8)
    cv2.putText(img, f"SIMULATED {pattern} t={t:5.1f}s", (24, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (200, 200, 200), 2)
    roi = (cam.rois.get("feed_inlet") if cam else None) or [420, 310, 860, 620]
    x1, y1, x2, y2 = roi
    if pattern in {"normal", "feed_jam", "idle"}:
        cv2.rectangle(img, (x1, y1), (x2, y2), (90, 90, 90), 2)              # 送料口
        cv2.rectangle(img, (x1 - 300, (y1 + y2) // 2 - 6), (x2 + 300, (y1 + y2) // 2 + 6), (70, 70, 70), -1)
        jammed = pattern == "feed_jam" and t >= jam_at_s
        if pattern == "idle":
            cv2.rectangle(img, (x1 + 40, y1 + 120), (x1 + 220, y1 + 180), (170, 170, 170), -1)
        elif not jammed:
            span = (x2 - x1) + 200
            off = int((t * 120) % span)                                        # 板材持續送入
            for k in range(3):
                sx = x1 - 100 + (off + k * span // 3) % span
                cv2.rectangle(img, (sx, y1 + 100 + 30 * k), (sx + 180, y1 + 150 + 30 * k), (180, 180, 180), -1)
            cv2.circle(img, (x1 - 150, (y1 + y2) // 2), 40, (120, 120, 120), 3)
            a = t * 4
            cv2.line(img, (x1 - 150, (y1 + y2) // 2),
                     (int(x1 - 150 + 40 * math.cos(a)), int((y1 + y2) // 2 + 40 * math.sin(a))), (200, 200, 200), 3)
        else:                                                                   # 卡料：歪斜堆疊的板材
            cx, cy = (x1 + x2) // 2, y1 + 150
            for k, ang in enumerate((18, -12, 30)):
                box = cv2.boxPoints(((cx + k * 25, cy - k * 20), (220, 50), ang)).astype(np.int32)
                cv2.fillPoly(img, [box], (160 + k * 20, 160, 140))
    elif pattern in {"ppe", "workers"}:
        for k, helmet in enumerate((True, pattern == "workers" or t < 5.0)):   # ppe：第二人 5 秒後脫帽
            px = 380 + k * 420 + int(20 * math.sin(t + k))
            cv2.rectangle(img, (px - 60, 300), (px + 60, 600), (110, 130, 150), -1)
            cv2.circle(img, (px, 250), 45, (150, 170, 200), -1)
            if helmet:
                cv2.ellipse(img, (px, 230), (55, 35), 0, 180, 360, (0, 200, 255), -1)
    elif pattern in {"weld_cell", "weld_smoke"}:
        wx1, wy1, wx2, wy2 = (cam.rois.get("weld_cell") if cam else None) or [380, 160, 900, 600]
        cv2.rectangle(img, (wx1, wy1), (wx2, wy2), (0, 200, 255), 2)                 # 工位 ROI（黃框）
        cx, cy = (wx1 + wx2) // 2, (wy1 + wy2) // 2
        stopped = pattern == "weld_smoke" and t >= max(0.0, jam_at_s - 3)          # 冒煙前 3 秒機器人停止
        ang = 0.0 if stopped else math.sin(t * 2) * 0.6
        ex, ey = int(cx + 140 * math.cos(ang)), int(cy - 60 + 140 * math.sin(ang))
        cv2.line(img, (cx, wy2 - 40), (ex, ey), (150, 150, 160), 18)                 # 機器人手臂
        if not stopped:                                                              # 工位內火花（正常）
            for k in range(6):
                cv2.circle(img, (ex + int(10 * math.sin(t * 9 + k)), ey + int(10 * math.cos(t * 7 + k))), 3,
                           (80, 220, 255), -1)
        if pattern == "weld_smoke" and t >= jam_at_s:                               # 工位外煙霧
            r = int(40 + 20 * min(t - jam_at_s, 6))
            overlay = img.copy()
            cv2.circle(overlay, (wx2 + 150, wy1 + 40), r, (170, 170, 170), -1)
            cv2.circle(overlay, (wx2 + 110, wy1 - 10), int(r * 0.7), (190, 190, 190), -1)
            img = cv2.addWeighted(overlay, 0.6, img, 0.4, 0)
    elif pattern == "station":
        cv2.rectangle(img, (420, 260), (860, 560), (90, 110, 130), -1)               # 工作台與工件
        x = 480 + int((t * 60) % 320)
        cv2.rectangle(img, (x, 180), (x + 30, 300), (170, 170, 170), -1)             # 起子
    elif pattern == "offline":
        cv2.putText(img, "NO SIGNAL", (480, 360), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 4)
    return img


def guess_pattern(video: str | None, cam: Camera | None) -> str:
    name = (video or "").lower()
    for key in ("feed_jam", "idle", "weld_smoke"):
        if key in name:
            return key
    if "ppe" in name or "violation" in name:
        return "ppe"
    if cam and "ppe" in cam.detectors:
        return "workers"
    if cam and cam.line == "welding":
        return "weld_cell" if "weld_cell" in cam.rois else "station"
    if cam and cam.line == "assembly":
        return "station"
    return "normal"


class VirtualCamera(CameraSource):
    def __init__(self, cam: Camera, bus: EventBus, clock: Clock, videos_root: Path, fps: float = 15):
        self.cam = cam
        self.camera_id = cam.id
        self.bus = bus
        self.clock = clock
        self.videos_root = videos_root
        self.fps = fps
        self.loop = True
        self.video: str | None = None
        self.then: str | None = None
        self.synthetic: str | None = None
        self.jam_at_s = 10.0
        self._cap: cv2.VideoCapture | None = None
        self._frame_no = 0
        self._latest: np.ndarray | None = None
        self._play_task: asyncio.Task | None = None
        self.set_source(cam.default_video, synthetic=None)

    # ── 來源設定 ───────────────────────────────────────────────
    def set_source(self, video: str | None, *, then: str | None = None, synthetic: str | None = None,
                   loop: bool = True, jam_at_s: float | None = None) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.video, self.then, self.loop = video, then, loop
        self._frame_no = 0
        if jam_at_s is not None:
            self.jam_at_s = jam_at_s
        self.synthetic = synthetic
        self.online = True
        if video:                                          # 有影片就播影片；synthetic 只是找不到影片時的樣式
            path = Path(video) if Path(video).is_absolute() else self.videos_root / video
            if path.exists():
                cap = cv2.VideoCapture(str(path))
                if cap.isOpened():
                    self._cap = cap
                    return
                log.warning("%s 影片無法開啟：%s", self.camera_id, path)
                self.online = False
                return
            log.info("%s 找不到影片 %s，改用合成畫面", self.camera_id, video)
        self.synthetic = synthetic or guess_pattern(video, self.cam)

    async def play(self, video_path: str, loop: bool = False, fps: float = 15) -> None:
        self.fps = fps
        self.set_source(video_path, loop=loop)
        await self.run()

    async def switch(self, video_path: str) -> None:
        self.set_source(video_path, loop=self.loop)

    def latest_frame(self) -> np.ndarray | None:
        return self._latest

    @property
    def video_t(self) -> float:
        return self._frame_no / self.fps

    def describe(self) -> dict:
        return {"camera_id": self.camera_id, "video": self.video, "synthetic": self.synthetic,
                "online": self.online, "fps": self.fps, "t": round(self.video_t, 2)}

    # ── 讀幀 ───────────────────────────────────────────────────
    def next_frame(self) -> np.ndarray | None:
        if self._cap is not None:
            ok, frame = self._cap.read()
            if not ok:
                if self.then:
                    self.set_source(self.then, loop=True)
                    return self.next_frame()
                if self.loop:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._frame_no = 0
                    ok, frame = self._cap.read()
                if not ok:
                    self.online = False
                    return None
            self._frame_no += 1
            if frame.shape[1] != FRAME_W or frame.shape[0] != FRAME_H:
                frame = cv2.resize(frame, (FRAME_W, FRAME_H))
            return frame
        if not self.online:
            return None
        frame = synthetic_frame(self.synthetic or "normal", self.video_t, self.cam, self.jam_at_s)
        self._frame_no += 1
        return frame

    async def emit(self) -> None:
        was_online = self.online
        frame = self.next_frame()
        now = self.clock.now()
        if frame is None:
            if was_online or self._latest is not None:
                self._latest = synthetic_frame("offline", self.video_t)
                await self.bus.publish(Event(ts=now, topic=Topic.VISION, source="sim.camera",
                                             line=self.cam.line, machine_id=self.cam.machine_id,
                                             camera_id=self.camera_id, type="camera_offline",
                                             payload={"video": self.video}))
            return
        self._latest = frame
        await self.bus.publish(Event(ts=now, topic=Topic.FRAMES, source="sim.camera", line=self.cam.line,
                                     machine_id=self.cam.machine_id, camera_id=self.camera_id, type="frame",
                                     payload={"frame": frame, "frame_no": self._frame_no}))

    async def run(self) -> None:
        """即時播放迴圈。"""
        while True:
            await self.emit()
            await asyncio.sleep(1.0 / self.fps)


class RtspCamera(CameraSource):
    """RTSP 實體攝影機（正式導入時使用；M1 不實作）。"""

    def __init__(self, camera_id: str, url: str):
        self.camera_id, self.url = camera_id, url

    async def play(self, video_path: str, loop: bool = False, fps: float = 15) -> None:
        raise NotImplementedError("RtspCamera 尚未實作（M2 之後）")

    async def switch(self, video_path: str) -> None:
        raise NotImplementedError

    def latest_frame(self) -> np.ndarray | None:
        return None

    async def emit(self) -> None:
        raise NotImplementedError
