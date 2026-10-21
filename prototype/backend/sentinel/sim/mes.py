"""MES Log 模擬器（IN-2）。

每台機器每秒一筆 mes_log：spm、feed_rate（次／分，約等於 spm）、feed_length_error_mm、motor_temp_c、
motor_current_a、tonnage_peak、state（running／idle／alarm）、alarm_code，以及 knowledge/signals.yaml
的其他訊號（依機型）。正常值加雜訊；劇本可在指定時間注入 set（跳變）、ramp（斜坡）與 event
（coil_change／die_change／param_change）。alarm_code 由空變有值時另發 machine_alarm。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from ..bus import EventBus
from ..clock import Clock
from ..schemas.events import Event, Topic
from ..schemas.plant import Machine, Plant

# 訊號名稱對齊 knowledge/signals.yaml（mes 區段）；正常值都是模擬值
SIGNALS = ["spm", "feed_rate", "feed_length_error_mm", "motor_temp_c", "motor_current_a", "tonnage_peak",
           "state", "alarm_code", "tonnage_balance_pct", "stroke_count_since_regrind", "die_stroke_total",
           "stop_angle_deg", "stop_time_ms", "air_pressure_bar", "counterbalance_bar", "shut_height_mm",
           "lube_pressure_bar", "bearing_temp_c", "die_lube_pulse_per_min", "scrap_conveyor_current_a",
           "light_curtain_status", "pilot_release_angle_deg", "straightener_setting_mm",
           # 銲接
           "wire_feed_speed_mpm", "wire_feed_motor_current_a", "arc_current_a", "arc_current_cv_pct", "arc_voltage_v",
           "arc_voltage_cv_pct", "arc_start_retry_per_100", "tip_arc_on_min", "gas_flow_lpm", "gas_supply_bar",
           "torch_clean_interval_min", "tcp_check_delta_mm", "defect_rate_pct", "weld_current_ka",
           "electrode_force_kn", "weld_count_since_dress", "tip_dress_count", "coolant_flow_lpm",
           "coolant_return_temp_c", "exhaust_flow_m3h", "exhaust_dp_pa", "exhaust_fan_state", "robot_state",
           # 組裝
           "screw_result", "final_torque_nm", "final_angle_deg", "rundown_torque_nm", "nok_hole_id", "nok_rate_pct",
           "bit_cycle_count", "feeder_pick_fail_count", "screw_count_actual", "screw_count_required",
           "torque_audit_dev_pct", "insulation_mohm", "test_result", "esd_check"]
# 劇本可用 event 步驟注入的機台事件（signals.yaml 的 events 區段）
EVENT_TYPES = {"coil_change", "die_change", "param_change", "days_since_pm",
               "weld_defect", "collision_event", "consumable_change", "part_lot_change",
               "screw_lot_change", "calib_due_days", "ppe_stock_count"}
# 通用警報類別碼（signals.yaml alarm_codes；刻意不用真實廠牌的警報碼）
ALARM_CODES = {
    "MISFEED", "DIE_PROTECT", "STRIP_LIFT", "STRIP_EDGE", "OVERLOAD", "DOUBLE_BLANK", "OVERRUN", "AIR_LOW",
    "FD_OVERCURRENT", "FD_POS_DEV", "LOOP_FAULT", "SCRAP_CONV_OVERLOAD", "LC_FAULT", "DIE_LUBE_LOW",
    "CENTRAL_LUBE_LOW",
    "ARC_START_FAIL", "ARC_LOST", "WIRE_STICK", "WIRE_FEED_OVERLOAD", "GAS_LOW", "CLEAN_STATION_FAULT", "COLLISION",
    "TCP_CHECK_NG", "COOLANT_LOW", "WELD_OVERTEMP", "SPOT_CURRENT_DEV", "FUME_EXH_LOW",
    "SCREW_NOK", "FEEDER_TIMEOUT", "COUNT_MISMATCH", "CALIB_OVERDUE", "TEST_NG", "ESD_CHECK_FAIL",
}
# 運轉時累加的計數器：值為 "spm"（依沖次）或每秒增加量（模擬值）
COUNTER_RATE: dict[str, str | float] = {
    "stroke_count_since_regrind": "spm", "die_stroke_total": "spm",
    "tip_arc_on_min": 1 / 60 * 0.6,          # 起弧率約 60%
    "weld_count_since_dress": 0.3,
    "bit_cycle_count": 0.5,
}
COUNTERS = set(COUNTER_RATE)

_PRESS_COMMON: dict[str, tuple[Any, float]] = {
    "feed_length_error_mm": (0.02, 0.004), "tonnage_balance_pct": (3.0, 0.3),
    "stroke_count_since_regrind": (42_000, 0), "die_stroke_total": (310_000, 0),
    "stop_angle_deg": (10.0, 0.2), "stop_time_ms": (180, 2.0), "air_pressure_bar": (5.8, 0.03),
    "counterbalance_bar": (4.2, 0.01), "shut_height_mm": (412.30, 0.005), "lube_pressure_bar": (2.2, 0.03),
    "bearing_temp_c": (42.0, 0.2), "die_lube_pulse_per_min": (6, 0.1), "scrap_conveyor_current_a": (1.2, 0.02),
    "light_curtain_status": ("OK", 0),
}
# 機型 → 訊號正常值與雜訊標準差
PROFILES: dict[str, dict[str, tuple[Any, float]]] = {
    "servo_press": {"spm": (42, 0.3), "feed_rate": (42, 0.2), "motor_temp_c": (58, 0.4),
                    "motor_current_a": (110, 1.5), "tonnage_peak": (182, 2.0), **_PRESS_COMMON},
    "mechanical_power_press": {"spm": (32, 0.3), "feed_rate": (32, 0.2), "motor_temp_c": (55, 0.4),
                               "motor_current_a": (95, 1.5), "tonnage_peak": (150, 2.0), **_PRESS_COMMON},
    "servo_roll_feeder": {"spm": (42, 0.3), "feed_rate": (42, 0.2), "feed_length_error_mm": (0.02, 0.004),
                          "motor_temp_c": (46, 0.3), "motor_current_a": (6.0, 0.1), "tonnage_peak": (0, 0),
                          "air_pressure_bar": (5.8, 0.03), "pilot_release_angle_deg": (220, 0),
                          "straightener_setting_mm": (1.2, 0)},
    "robot_mig_cell": {
        "wire_feed_speed_mpm": (6.0, 0.05), "wire_feed_motor_current_a": (2.2, 0.05), "arc_current_a": (130, 2.0),
        "arc_current_cv_pct": (3.0, 0.2), "arc_voltage_v": (18.5, 0.1), "arc_voltage_cv_pct": (2.5, 0.2),
        "arc_start_retry_per_100": (1.0, 0.1), "tip_arc_on_min": (120, 0), "gas_flow_lpm": (17.0, 0.2),
        "gas_supply_bar": (4.0, 0.02), "torch_clean_interval_min": (12, 0), "tcp_check_delta_mm": (0.2, 0.01),
        "defect_rate_pct": (0.8, 0.05), "coolant_flow_lpm": (2.0, 0.03), "coolant_return_temp_c": (30.0, 0.2),
        "exhaust_flow_m3h": (2000, 15), "exhaust_dp_pa": (600, 5), "exhaust_fan_state": ("running", 0),
        "robot_state": ("running", 0)},
    "spot_welder": {
        "weld_current_ka": (10.0, 0.1), "electrode_force_kn": (2.5, 0.02), "weld_count_since_dress": (120, 0),
        "tip_dress_count": (8, 0), "coolant_flow_lpm": (5.0, 0.05), "coolant_return_temp_c": (30.0, 0.2),
        "defect_rate_pct": (0.8, 0.05), "exhaust_flow_m3h": (2000, 15), "exhaust_dp_pa": (600, 5),
        "exhaust_fan_state": ("running", 0)},
    "screwdriving_station": {
        "screw_result": ("OK", 0), "final_torque_nm": (0.80, 0.01), "final_angle_deg": (1440, 20),
        "rundown_torque_nm": (0.08, 0.005), "nok_hole_id": (None, 0), "nok_rate_pct": (0.4, 0.03),
        "bit_cycle_count": (18_000, 0), "feeder_pick_fail_count": (1, 0), "screw_count_actual": (12, 0),
        "screw_count_required": (12, 0), "torque_audit_dev_pct": (1.5, 0.1), "esd_check": ("PASS", 0)},
    "manual_assembly_station": {
        "insulation_mohm": (500, 5), "test_result": ("PASS", 0), "esd_check": ("PASS", 0),
        "screw_count_actual": (12, 0), "screw_count_required": (12, 0), "nok_rate_pct": (0.3, 0.03)},
}
DEFAULT_PROFILE = {"spm": (0, 0), "feed_rate": (0, 0), "feed_length_error_mm": (0, 0),
                   "motor_temp_c": (40, 0.3), "motor_current_a": (5, 0.1), "tonnage_peak": (0, 0)}


@dataclass
class Ramp:
    signal: str
    start: float
    end: float
    t0: float
    over_s: float


@dataclass
class MachineSim:
    machine: Machine
    base: dict[str, Any] = field(default_factory=dict)
    sigma: dict[str, float] = field(default_factory=dict)
    ramps: list[Ramp] = field(default_factory=list)
    script: list[dict] = field(default_factory=list)
    applied: int = 0
    pending_events: list[dict] = field(default_factory=list)
    last_t: float | None = None

    @classmethod
    def for_machine(cls, m: Machine) -> "MachineSim":
        prof = PROFILES.get(m.type, DEFAULT_PROFILE)
        sim = cls(machine=m)
        sim.reset()
        sim.sigma = {k: v[1] for k, v in prof.items()}
        return sim

    def reset(self) -> None:
        prof = PROFILES.get(self.machine.type, DEFAULT_PROFILE)
        self.base = {k: v[0] for k, v in prof.items()} | {"state": "running", "alarm_code": None}
        self.ramps, self.script, self.applied, self.pending_events, self.last_t = [], [], 0, [], None

    def apply_script(self, t: float) -> list[str]:
        """套用 t 之前尚未執行的劇本步驟；回傳變更的訊號名稱。"""
        changed: list[str] = []
        while self.applied < len(self.script) and self.script[self.applied]["t"] <= t:
            step = self.script[self.applied]
            self.applied += 1
            if step.get("event"):                       # 例如 {type: coil_change, payload: {...}}
                self.pending_events.append(dict(step["event"]))
            for k, v in (step.get("set") or {}).items():
                self.base[k] = v
                self.ramps = [r for r in self.ramps if r.signal != k]
                changed.append(k)
            ramp = dict(step.get("ramp") or {})
            over_s = float(ramp.pop("over_s", step.get("over_s", 5)))   # 劇本寫在 ramp 裡
            for k, spec in ramp.items():
                start, end = (spec[0], spec[1]) if isinstance(spec, list) else (self.base[k], spec)
                self.ramps = [r for r in self.ramps if r.signal != k]
                self.ramps.append(Ramp(k, float(start), float(end), float(step["t"]), over_s))
                changed.append(k)
        return changed

    def sample(self, t: float, rng: random.Random) -> dict[str, Any]:
        values = dict(self.base)
        for r in self.ramps:
            frac = min(max((t - r.t0) / r.over_s, 0.0), 1.0) if r.over_s > 0 else 1.0
            values[r.signal] = r.start + (r.end - r.start) * frac
            if frac >= 1.0:
                self.base[r.signal] = r.end
        running = values.get("state") == "running"
        dt = 0.0 if self.last_t is None else max(0.0, t - self.last_t)
        self.last_t = t
        for k in COUNTERS & set(self.base):
            if running:
                rate = COUNTER_RATE[k]
                self.base[k] += (float(self.base.get("spm") or 0) / 60 if rate == "spm" else float(rate)) * dt
            values[k] = int(self.base[k])
        for k, sd in self.sigma.items():
            v = values.get(k)
            if k not in COUNTERS and isinstance(v, (int, float)) and v != 0 and sd > 0 and running:
                values[k] = v + rng.gauss(0, sd)
        if not running and "spm" in values:   # 停機時節拍歸零
            values["spm"] = 0
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in values.items()}


class MesSimulator:
    def __init__(self, plant: Plant, bus: EventBus, clock: Clock, seed: int = 42):
        self.bus = bus
        self.clock = clock
        self.plant = plant
        self.rng = random.Random(seed)
        self.sims = {m.id: MachineSim.for_machine(m) for m in plant.machines.values()}
        self.last: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        for sim in self.sims.values():
            sim.reset()

    def load_script(self, script: dict[str, list[dict]]) -> None:
        self.reset()
        for machine_id, steps in (script or {}).items():
            if machine_id in self.sims:
                self.sims[machine_id].script = sorted(steps, key=lambda s: s["t"])

    async def tick(self, t: float) -> None:
        """t：劇本開始後的秒數。每台機器發一筆 mes_log；alarm_code 由空變有值時另發 machine_alarm。"""
        now = self.clock.now()
        for mid, sim in self.sims.items():
            sim.apply_script(t)
            values = sim.sample(t, self.rng)
            prev = self.last.get(mid, {})
            self.last[mid] = values
            m = sim.machine
            await self.bus.publish(Event(ts=now, topic=Topic.MES, source="sim.mes", line=m.line,
                                         machine_id=mid, type="mes_log", payload=values))
            for ev in sim.pending_events:
                await self.bus.publish(Event(ts=now, topic=Topic.MES, source="sim.mes", line=m.line, machine_id=mid,
                                             type=str(ev.get("type")), payload=dict(ev.get("payload") or {})))
            sim.pending_events.clear()
            if values.get("alarm_code") and values.get("alarm_code") != prev.get("alarm_code"):
                await self.bus.publish(Event(ts=now, topic=Topic.MES, source="sim.mes", line=m.line,
                                             machine_id=mid, type="machine_alarm",
                                             payload={"alarm_code": values["alarm_code"],
                                                      "feed_rate": values.get("feed_rate"), "state": values.get("state")}))
