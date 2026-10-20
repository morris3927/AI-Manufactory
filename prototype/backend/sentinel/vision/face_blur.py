"""人臉模糊（SE-6）：送出或儲存人員畫面前先模糊。找不到臉時保守地模糊頭部區域（上方 1/3）。"""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np


@lru_cache(maxsize=1)
def _cascade() -> cv2.CascadeClassifier | None:
    try:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore[attr-defined]
    except AttributeError:
        return None
    c = cv2.CascadeClassifier(path)
    return None if c.empty() else c


def blur_faces(img: np.ndarray, fallback_head: bool = True) -> tuple[np.ndarray, int]:
    out = img.copy()
    h, w = out.shape[:2]
    faces: list[tuple[int, int, int, int]] = []
    c = _cascade()
    if c is not None and min(h, w) >= 24:
        gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        faces = [tuple(map(int, f)) for f in c.detectMultiScale(gray, 1.1, 4, minSize=(16, 16))]
    if not faces and fallback_head:
        faces = [(0, 0, w, max(1, h // 3))]
    for x, y, fw, fh in faces:
        roi = out[y:y + fh, x:x + fw]
        if roi.size:
            k = max(15, (min(fw, fh) // 3) | 1)
            out[y:y + fh, x:x + fw] = cv2.GaussianBlur(roi, (k, k), 0)
    return out, len(faces)
