"""機台運轉／閒置（SE-2）：灰階 → ROI 幀差 → 指數移動平均；門檻寫在 plant.yaml。

motion_score = ROI 內變化像素的百分比（幀差 > 20 灰階），比平均差值對雜訊更穩定。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..schemas.plant import MotionCfg


class MotionDetector:
    def __init__(self, roi: list[int] | None, cfg: MotionCfg):
        self.roi = roi
        self.cfg = cfg
        self._prev: np.ndarray | None = None
        self.ema: float = 0.0
        self.state: str | None = None

    def update(self, frame: np.ndarray) -> tuple[str | None, float, bool]:
        """回傳 (state, motion_score, changed)。第一幀只建立基準。"""
        img = frame
        if self.roi:
            x1, y1, x2, y2 = self.roi
            img = frame[y1:y2, x1:x2]
        gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        if self._prev is None or self._prev.shape != gray.shape:
            self._prev = gray
            return self.state, self.ema, False
        diff = float((cv2.absdiff(gray, self._prev) > 20).mean() * 100)
        self._prev = gray
        self.ema = self.cfg.alpha * diff + (1 - self.cfg.alpha) * self.ema
        new = self.state
        if self.ema >= self.cfg.on:
            new = "running"
        elif self.ema <= self.cfg.off:
            new = "idle"
        elif self.state is None:
            new = "idle"
        changed = new != self.state
        self.state = new
        return new, self.ema, changed
