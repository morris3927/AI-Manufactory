"""組裝線規則。"""
from __future__ import annotations

from .engine import Finding, rule
from .windows import Windows


@rule(id="R-ASM-PPE", line="assembly", category="safety", default_severity="S2", entity_kind="camera")
def ppe_no_helmet(w: Windows, cam: str) -> Finding | None:
    """作業區偵測到未戴安全帽（YOLO：Head 與 Helmet 重疊 < 0.3；同一人 3 秒內不重複）。"""
    th = w.th
    win = w.vision(cam, "ppe_violation")
    v = win.latest(last_s=th.ppe_window_s)
    if not v or (v.get("conf") or 0) < th.ppe_conf:
        return None
    return Finding(type="ppe_violation", facts={
        "source": "vision", "missing": v.get("missing", ["helmet"]), "conf": v.get("conf"),
        "detector": v.get("detector"), "violations_last_60s": win.count(60), "person_box": v.get("person_box"),
    })
