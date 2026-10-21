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


STOPPED = {"idle", "alarm"}


@rule(id="R-PLANT-MULTI-STOP", line="*", category="equipment", default_severity="S3")
def multi_stop(w: Windows, m: str) -> Finding | None:
    """RU-1：不同連動群組的 2 台以上機台在時間窗內同時停止，且有氣源壓力低 → S3（例如廠務空壓異常）。

    送料機與下游沖床屬同一連動群組（plant.yaml interlock_groups），卡料時一起停是 S2，不算在這裡。
    只在「停止機台中 ID 最小的那台」開單，避免每台各開一張。
    """
    th, plant = w.th, w.plant
    if plant is None:
        return None
    window = th.multi_stop_window_s
    stopped = []
    for e in sorted(w.entities()):
        st = w.mes(e, "state")
        if e in plant.machines and st.latest() in STOPPED and st.changed_within(window):
            stopped.append(e)
    groups = {plant.interlock_group(e) for e in stopped}
    if len(groups) < 2 or m != stopped[0]:
        return None
    air = {e: w.mes(e, "air_pressure_bar").latest() for e in stopped}
    low = {e: v for e, v in air.items() if isinstance(v, (int, float)) and v < th.air_pressure_low_bar}
    if not low:
        return None
    return Finding(type="multi_stop", facts={
        "source": "mes", "stopped_machines": stopped, "interlock_groups": sorted(groups),
        "groups_stopped": len(groups), "air_pressure_low": low, "air_low_threshold_bar": th.air_pressure_low_bar,
        "alarm_codes": {e: w.mes(e, "alarm_code").latest() for e in stopped}, "window_s": window,
    })
