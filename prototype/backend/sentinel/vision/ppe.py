"""PPE 偵測（SE-1）。

- YOLO 延遲載入（ultralytics 為選配套件；權重放 prototype/models/）。
- 判斷：偵測到 Head，且 Head 框與任何 Helmet 框的重疊比例（交集／Head 面積）< 0.3 → 未戴安全帽。
- 同一人 3 秒內不重複發（以 IoU 追蹤）。
- 沒有權重時可用 ScriptedPpeDetector 依劇本重播偵測結果，讓流程能跑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]   # x1, y1, x2, y2（像素）


@dataclass
class Detection:
    cls: str                 # head／helmet／person（已正規化）
    box: Box
    conf: float


@dataclass
class PpeViolation:
    person_box: Box
    head_box: Box
    missing: list[str]
    conf: float
    overlap: float


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def overlap_ratio(head: Box, helmet: Box) -> float:
    a = area(head)
    return intersection(head, helmet) / a if a > 0 else 0.0


def find_violations(dets: list[Detection], overlap_thr: float = 0.3, conf_min: float = 0.5) -> list[PpeViolation]:
    heads = [d for d in dets if d.cls == "head" and d.conf >= conf_min]
    helmets = [d for d in dets if d.cls == "helmet"]
    persons = [d for d in dets if d.cls == "person"]
    out: list[PpeViolation] = []
    for h in heads:
        best = max((overlap_ratio(h.box, hm.box) for hm in helmets), default=0.0)
        if best >= overlap_thr:
            continue
        person = next((p.box for p in persons if intersection(h.box, p.box) >= 0.8 * area(h.box)), None)
        if person is None:   # 沒偵測到人：用頭部框往下延伸估計
            x1, y1, x2, y2 = h.box
            hh = y2 - y1
            person = (x1 - hh, y1, x2 + hh, y2 + 5 * hh)
        out.append(PpeViolation(person_box=person, head_box=h.box, missing=["helmet"], conf=h.conf, overlap=best))
    return out


class PpeDetector(Protocol):
    name: str

    def detect(self, frame: np.ndarray, t: float) -> list[Detection]: ...


# 模型類別名稱 → 正規化類別。2 類自訓模型 {0: head, 1: helmet}、SH17（17 類，含 head／helmet／person）、
# 常見 PPE 資料集（Hardhat／NO-Hardhat）都對得到；其他類別（vest、gloves…）忽略。
DEFAULT_CLASS_ALIASES = {
    "head": "head", "no_helmet": "head", "no-hardhat": "head", "no-helmet": "head", "without_helmet": "head",
    "helmet": "helmet", "hardhat": "helmet", "hard-hat": "helmet", "with_helmet": "helmet",
    "person": "person",
}


class YoloPpeDetector:
    name = "yolo"

    def __init__(self, weights: Path, device: str = "mps", class_map: dict[str, list[str]] | None = None,
                 conf: float = 0.25):
        self.weights, self.conf = weights, conf
        self.device = self._pick_device(device)
        self.aliases = dict(DEFAULT_CLASS_ALIASES)
        for norm, names in (class_map or {}).items():
            for n in names:
                self.aliases[n.lower()] = norm
        self._model = None
        self.class_names: dict[int, str] = {}

    @staticmethod
    def _pick_device(device: str) -> str:
        if device == "mps":
            try:
                import torch

                if not torch.backends.mps.is_available():
                    return "cpu"
            except ImportError:
                return "cpu"
        return device

    @staticmethod
    def available(weights: Path) -> bool:
        if not weights.exists():
            return False
        try:
            import ultralytics  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO  # 延遲載入

            self._model = YOLO(str(self.weights))
            self.class_names = dict(self._model.names)
            log.info("YOLO PPE 模型載入：%s（%s）類別=%s", self.weights.name, self.device, self.class_names)
        return self._model

    def normalize(self, raw: str) -> str | None:
        return self.aliases.get(raw.lower())

    def detect(self, frame: np.ndarray, t: float) -> list[Detection]:
        model = self._load()
        res = model.predict(frame, device=self.device, conf=self.conf, verbose=False)[0]
        out: list[Detection] = []
        for b in res.boxes:
            cls = self.normalize(res.names[int(b.cls)])
            if cls:
                out.append(Detection(cls, tuple(float(v) for v in b.xyxy[0].tolist()), float(b.conf)))
        return out


@dataclass
class ScriptedPpeDetector:
    """依劇本時間重播偵測結果（沒有 YOLO 權重時的替代；結果標示為模擬）。"""

    script: list[tuple[float, float, list[Detection]]] = field(default_factory=list)
    name: str = "scripted"

    def detect(self, frame: np.ndarray, t: float) -> list[Detection]:
        for t_from, t_to, dets in self.script:
            if t_from <= t < t_to:
                return list(dets)
        return []


class ViolationTracker:
    """同一人 dedupe_s 秒內不重複發。"""

    def __init__(self, dedupe_s: float = 3.0, iou_thr: float = 0.3):
        self.dedupe_s, self.iou_thr = dedupe_s, iou_thr
        self._recent: list[tuple[float, Box]] = []

    def should_emit(self, v: PpeViolation, t: float) -> bool:
        self._recent = [(ts, b) for ts, b in self._recent if t - ts < self.dedupe_s]
        for i, (ts, b) in enumerate(self._recent):
            if iou(b, v.person_box) >= self.iou_thr:
                self._recent[i] = (ts, v.person_box)     # 更新位置，不更新時間
                return False
        self._recent.append((t, v.person_box))
        return True
