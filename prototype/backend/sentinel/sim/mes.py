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
           "light_curtain_status", "pilot_release_angle_deg", "straightener_setting_mm"]
EVENT_TYPES = {"coil_change", "die_change", "param_change"}   # 劇本可用 event 步驟注入
COUNTERS = {"stroke_count_since_regrind", "die_stroke_total"}   # 運轉時依 spm 累加

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
                self.base[k] = self.base[k] + float(self.base.get("spm") or 0) / 60 * dt
            values[k] = int(self.base[k])
        for k, sd in self.sigma.items():
            v = values.get(k)
            if k not in COUNTERS and isinstance(v, (int, float)) and v != 0 and sd > 0 and running:
                values[k] = v + rng.gauss(0, sd)
        if not running:                    # 停機時節拍歸零
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
