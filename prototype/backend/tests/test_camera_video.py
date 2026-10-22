"""VirtualCamera 播放真實影片檔：影片時間要跟模擬時間一致（逐步重播 fps 與檔案 fps 不同時跳幀）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from sentinel.bus import EventBus
from sentinel.clock import TW, ManualClock
from sentinel.schemas.plant import Camera
from sentinel.sim.camera import VirtualCamera


def _write_counter_video(path: Path, n: int = 45, fps: int = 15) -> None:
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1280, 720))
    for i in range(n):                      # 每幀亮度 = 幀號 × 5（用來辨識讀到第幾幀）
        vw.write(np.full((720, 1280, 3), i * 5, np.uint8))
    vw.release()


def test_video_time_follows_sim_fps(tmp_path: Path):
    _write_counter_video(tmp_path / "clip.mp4")
    cam = VirtualCamera(Camera(id="CAM-T", default_video=None), EventBus(), ManualClock(datetime(2026, 10, 30, 9, tzinfo=TW)),
                        tmp_path, fps=3)
    cam.set_source("clip.mp4", synthetic="ppe")                         # synthetic 只是找不到影片時的退路
    assert cam.synthetic is None, "影片存在時不該用合成畫面"
    levels = [float(cam.next_frame().mean()) for _ in range(4)]       # 3 fps 讀 15 fps 檔案：每次前進 5 幀
    got = [v / 5 for v in levels]                                      # mp4v 壓縮會讓亮度差幾階，容許 ±1 幀
    assert all(abs(g - want) <= 1 for g, want in zip(got, [0, 5, 10, 15])), got
    assert abs(cam.video_t - 4 / 3) < 1e-6


def test_missing_video_falls_back_to_synthetic(tmp_path: Path):
    cam = VirtualCamera(Camera(id="CAM-T"), EventBus(), ManualClock(datetime(2026, 10, 30, 9, tzinfo=TW)), tmp_path)
    cam.set_source("nope.mp4", synthetic="feed_jam")
    assert cam.synthetic == "feed_jam" and cam.next_frame().shape == (720, 1280, 3)
