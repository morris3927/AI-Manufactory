"""跨模態規則。"""
from __future__ import annotations

from .engine import Finding, rule
from .windows import Windows


@rule(id="R-STP-LOG-MISMATCH", line="stamping", category="equipment", default_severity="S2")
def log_mismatch(w: Windows, m: str) -> Finding | None:
    """RU-2①：影像判定閒置，但 Log 回報運轉，持續 N 秒 → Log 失準（S2）。"""
    th = w.th
    win = w.vision(m, "machine_state")
    ms = win.latest()
    ts = win.latest_ts()
    if not ms or ms.get("state") != "idle" or ts is None:
        return None
    idle_s = (w.clock.now() - ts).total_seconds()
    running_s = w.mes(m, "state").duration_true(lambda s: s == "running")
    if idle_s >= th.log_mismatch_s and running_s >= th.log_mismatch_s:
        return Finding(type="log_mismatch", facts={
            "source": "fused", "vision_state": "idle", "log_state": "running",
            "vision_idle_s": round(idle_s, 1), "log_running_s": round(running_s, 1),
            "log_spm": w.mes(m, "spm").latest(), "over_threshold": True,
        })
    return None
