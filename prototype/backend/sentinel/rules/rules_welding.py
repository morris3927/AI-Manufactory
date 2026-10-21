"""銲接線規則。工位 ROI 內的火花與銲煙屬正常；只看工位外（01_初選問題處理.md C1）。"""
from __future__ import annotations

from .engine import Finding, rule
from .windows import Windows

ROBOT_STOPPED = {"stopped", "cycle_stop", "estop", "teach"}


@rule(id="R-WLD-FIRE", line="welding", category="safety", default_severity="S3",
      machine_types=("robot_mig_cell", "spot_welder"))
def fire_suspected(w: Windows, m: str) -> Finding | None:
    """機器人停止後工位外仍冒煙，或工位外有火光 → 疑似火災（S3，走緊急應變，不是維修 SOP）。"""
    th = w.th
    smoke = w.vision(m, "smoke").latest(last_s=th.smoke_window_s)
    flame = w.vision(m, "flame").latest(last_s=th.smoke_window_s)
    robot = w.mes(m, "robot_state")
    stopped = robot.latest() in ROBOT_STOPPED or w.mes(m, "state").latest() in {"idle", "alarm"}
    stopped_s = robot.duration_true(lambda s: s in ROBOT_STOPPED) if robot.latest() is not None else None
    outside = bool(smoke and smoke.get("value") == "outside_cell")
    if not ((outside and stopped) or (flame and flame.get("value"))):
        return None
    return Finding(type="fire_suspected", facts={
        "source": "fused", "smoke": smoke.get("value") if smoke else None,
        "p_smoke_outside": smoke.get("p_smoke_outside") if smoke else None,
        "flame": bool(flame and flame.get("value")), "robot_state": robot.latest(),
        "robot_stopped_s": round(stopped_s, 1) if stopped_s is not None else None,
        "exhaust_fan_state": w.mes(m, "exhaust_fan_state").latest(),
        "exhaust_flow_m3h": w.mes(m, "exhaust_flow_m3h").latest(), "smoke_box": smoke.get("box") if smoke else None,
    })
