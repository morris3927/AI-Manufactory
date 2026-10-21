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


@rule(id="R-ASM-SCREW-NOK", line="assembly", category="equipment", default_severity="S2",
      machine_types=("screwdriving_station",))
def screw_nok(w: Windows, m: str) -> Finding | None:
    """電動起子鎖附 NOK 持續 N 秒，且近 100 次 NOK 率超過門檻 → 鎖附異常（S2，啟動 AI 診斷：扭力、離合器、校正）。"""
    th = w.th
    rate = w.mes(m, "nok_rate_pct").latest()
    res = w.mes(m, "screw_result")
    nok_s = res.duration_true(lambda v: isinstance(v, str) and v.startswith("NOK"))
    if not isinstance(rate, (int, float)) or rate <= th.screw_nok_rate_pct or nok_s < th.screw_nok_s:
        return None
    calib = w.events(m, "calib_due_days").latest()
    return Finding(type="screw_nok", facts={
        "source": "mes", "screw_result": res.latest(), "nok_rate_pct": round(rate, 2), "nok_seconds": round(nok_s, 1),
        "final_torque_nm": w.mes(m, "final_torque_nm").latest(), "final_angle_deg": w.mes(m, "final_angle_deg").latest(),
        "torque_target_nm": 0.8, "torque_below_target_pct": round((1 - (w.mes(m, "final_torque_nm").latest() or 0.8) / 0.8) * 100, 1),
        "alarm_code": w.mes(m, "alarm_code").latest(),
        "calib_due_days": calib.get("value") if calib else None,
        "calib_overdue": bool(calib and isinstance(calib.get("value"), (int, float)) and calib["value"] < 0),
        "bit_cycle_count": w.mes(m, "bit_cycle_count").latest(),
    })
