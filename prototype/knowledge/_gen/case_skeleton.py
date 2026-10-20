"""Deterministic skeletons for synthetic stamping repair cases (07_RAG知識庫資料.md §6.2).

The PROGRAM decides every fact: root cause, case type, wrong first hypothesis (always drawn
from distinguish_from), machine, timestamps / shift, PM calendar (days_since_pm consistent per
machine), recent events, material, die, log signals (shape follows failure_modes log_signals),
trigger, downtime, safety flags. The LLM later only WRITES text from this skeleton.
"""
from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict

from common import MACHINES, catalog

TZ = dt.timezone(dt.timedelta(hours=8))
START, END = dt.date(2025, 10, 1), dt.date(2026, 9, 30)
PM_CYCLE = 60  # 模擬值
HIGH_FREQ = ["feed_roller_wear", "material_thickness_dev", "die_misalignment", "slug_pulling",
             "misfeed_sensor_fault"]

DIES = {  # die master (模擬資料)
    "D-1U-BASE-01": {"part": "1U 機殼底板", "grade": "SECC", "t": 1.0, "shut": 412.50, "upper_kg": 1350, "cb": 4.2, "pilot": 220, "ton": 168},
    "D-2U-TOP-03": {"part": "2U 上蓋", "grade": "SECC", "t": 0.8, "shut": 398.00, "upper_kg": 1180, "cb": 3.9, "pilot": 215, "ton": 142},
    "D-4U-SIDE-02": {"part": "4U 側板", "grade": "SGCC", "t": 1.2, "shut": 425.20, "upper_kg": 1620, "cb": 4.6, "pilot": 225, "ton": 186},
    "D-BRKT-HDD-07": {"part": "硬碟托架", "grade": "SGCC", "t": 1.0, "shut": 365.80, "upper_kg": 760, "cb": 3.1, "pilot": 210, "ton": 118},
    "D-RAIL-11": {"part": "滑軌內軌", "grade": "SGCC", "t": 1.6, "shut": 388.40, "upper_kg": 980, "cb": 3.5, "pilot": 230, "ton": 155},
}
DIES_BY_LINE = {"L1": ["D-1U-BASE-01", "D-2U-TOP-03", "D-4U-SIDE-02"], "L2": ["D-BRKT-HDD-07", "D-RAIL-11", "D-2U-TOP-03"]}

PARTS = {  # generic part vocabulary per mode (validator checks parts_replaced ⊂ this)
    "feed_roller_wear": ["feed_roll_upper", "feed_roll_lower", "feed_roll_bearing", "pinch_spring"],
    "material_thickness_dev": [],
    "die_misalignment": ["die_locating_pin", "locating_pin_bushing", "die_clamp_bolt"],
    "pilot_release_timing": ["pilot_pin", "pilot_release_cylinder_seal"],
    "coil_camber": ["strip_side_guide"],
    "slug_pulling": ["punch", "anti_slug_pin", "die_button"],
    "punch_edge_wear": ["punch", "die_insert"],
    "die_lubrication_insufficient": ["die_lube_nozzle", "die_lube_hose", "die_lube_filter"],
    "misfeed_sensor_fault": ["misfeed_sensor", "sensor_cable", "sensor_connector"],
    "stripper_spring_fatigue": ["stripper_spring_set"],
    "clutch_brake_wear": ["brake_lining", "clutch_friction_plate"],
    "overload_tonnage": ["punch", "pilot_pin", "stripper_plate_bolt"],
    "air_pressure_low": ["air_fitting_oring", "air_filter_element", "air_regulator"],
    "counterbalance_misset": ["counterbalance_hose", "air_regulator"],
    "servo_feeder_drive_alarm": ["drive_cooling_fan", "encoder_coupling", "feed_motor_bearing"],
    "straightener_misset": ["straightener_scraper"],
    "scrap_chute_jam": ["scrap_chute_guide", "scrap_conveyor_belt"],
    "light_curtain_fault": ["light_curtain_receiver", "light_curtain_cable"],
    "shut_height_misset": ["shut_height_lock_bolt"],
    "central_lube_fault": ["central_lube_filter", "lube_hose", "lube_pressure_switch"],
}
NEW_CANDIDATES = {  # plausible root causes NOT in the catalog → final_root_cause = "new"
    "feeder": [("uncoiler_brake_wear", "料架剎車來令片磨耗，料捲慣性過大造成料環甩動、送料瞬間張力變化"),
               ("strip_rust_oil_excess", "料捲供應商更換防鏽油品，油膜過厚造成滾輪打滑（滾輪本身正常）"),
               ("feed_sync_signal_fault", "送料機與沖床之間的送料起動同步訊號纜線接觸不良，送料時機偶發延遲")],
    "die": [("nitrogen_spring_leak", "模具氮氣彈簧漏氣，壓料力不足造成成形不穩"),
            ("die_guide_post_wear", "模具導柱／導套磨耗間隙過大，上下模對位隨沖次漂移"),
            ("die_clamp_pressure_low", "快速夾模器油壓不足，上模在生產中微量鬆動")],
    "press": [("slide_gib_clearance", "滑塊導軌（gib）間隙過大，偏載時滑塊傾斜"),
              ("servo_encoder_battery_low", "伺服馬達編碼器電池電壓低，停電後原點偏移"),
              ("main_motor_fan_fault", "主馬達冷卻風扇故障，連續生產時馬達溫度升高降載")],
    "lube": [("lube_oil_water_contamination", "潤滑油混入冷卻水乳化，潤滑膜不足")],
}
FAMILY = {"feed_roller_wear": "feeder", "material_thickness_dev": "feeder", "pilot_release_timing": "feeder",
          "coil_camber": "feeder", "misfeed_sensor_fault": "feeder", "servo_feeder_drive_alarm": "feeder",
          "straightener_misset": "feeder", "die_misalignment": "die", "slug_pulling": "die",
          "punch_edge_wear": "die", "die_lubrication_insufficient": "die", "stripper_spring_fatigue": "die",
          "overload_tonnage": "die", "scrap_chute_jam": "die", "shut_height_misset": "die",
          "clutch_brake_wear": "press", "air_pressure_low": "press", "counterbalance_misset": "press",
          "light_curtain_fault": "press", "central_lube_fault": "lube"}
DOWNTIME = {  # typical downtime range (min, 模擬值)
    "feed_roller_wear": (35, 75), "material_thickness_dev": (25, 70), "die_misalignment": (50, 130),
    "pilot_release_timing": (30, 80), "coil_camber": (30, 90), "slug_pulling": (25, 80),
    "punch_edge_wear": (90, 240), "die_lubrication_insufficient": (30, 70), "misfeed_sensor_fault": (15, 45),
    "stripper_spring_fatigue": (80, 180), "clutch_brake_wear": (180, 480), "overload_tonnage": (60, 200),
    "air_pressure_low": (20, 90), "counterbalance_misset": (25, 60), "servo_feeder_drive_alarm": (40, 120),
    "straightener_misset": (20, 55), "scrap_chute_jam": (20, 60), "light_curtain_fault": (40, 180),
    "shut_height_misset": (25, 70), "central_lube_fault": (60, 180),
}
TRIGGER = {  # (source, event_type, rule_severity)
    "feed_roller_wear": ("fused", "feed_jam_suspected", "S2"), "material_thickness_dev": ("mes", "misfeed_stop", "S2"),
    "die_misalignment": ("mes", "quality_defect", "S2"), "pilot_release_timing": ("rule", "quality_defect", "S2"),
    "coil_camber": ("mes", "strip_edge_alarm", "S2"), "slug_pulling": ("mes", "die_protect_stop", "S2"),
    "punch_edge_wear": ("rule", "quality_trend", "S1"), "die_lubrication_insufficient": ("mes", "die_lube_alarm", "S2"),
    "misfeed_sensor_fault": ("mes", "misfeed_stop", "S2"), "stripper_spring_fatigue": ("mes", "strip_lift_stop", "S2"),
    "clutch_brake_wear": ("mes", "overrun_alarm", "S2"), "overload_tonnage": ("mes", "overload_stop", "S2"),
    "air_pressure_low": ("mes", "air_low_alarm", "S2"), "counterbalance_misset": ("rule", "motor_current_abnormal", "S2"),
    "servo_feeder_drive_alarm": ("mes", "feeder_drive_alarm", "S2"), "straightener_misset": ("fused", "feed_jam_suspected", "S2"),
    "scrap_chute_jam": ("mes", "scrap_conveyor_alarm", "S2"), "light_curtain_fault": ("mes", "light_curtain_fault", "S2"),
    "shut_height_misset": ("rule", "quality_defect", "S2"), "central_lube_fault": ("mes", "lube_alarm", "S2"),
}
FALSE_ALARM_STORY = {
    "feeder": ["影像判斷疑似卡料，實為操作員換料捲時暫停送料", "送料速率歸零觸發規則，實為班長按暫停做首件檢查",
               "攝影機畫面被料架反光干擾，影像誤判料帶堆積"],
    "die": ["模具保護單次觸發，現場檢查無廢料、無異物，重置後連續生產正常", "品質站量測治具歸零錯誤，誤判毛邊超標",
            "首件檢查員誤用舊版圖面判定尺寸不良"],
    "press": ["感測值單點跳動（雜訊），持續 2 秒即恢復，現場檢查正常", "影像偵測到「人員在危險區」，實為光幕外側的搬運人員",
              "警報為計畫性保養前的測試訊號"],
    "lube": ["集中潤滑低油位開關在補油過程中短暫觸發", "溫度感測器接頭鬆動造成讀值跳動，實際軸承溫度正常"],
}
# Reviewer overrides (2026-10-21 reliability-eng review). Applied AFTER the random draw so the RNG
# stream — and therefore every other case — is unchanged.
NEW_MODE_OVERRIDE = {  # the family pick for these modes produced causes that cannot explain the trigger
    "air_pressure_low": ("counterbalance_seal_leak",
                         "平衡缸活塞密封件內漏，機台本身耗氣量異常增加，連續運轉時機台端氣壓下降（外部皂液測漏查不到）"),
}
FA_STORY_OVERRIDE = {  # false-alarm story must be able to produce the trigger signal
    "slug_pulling": "模具保護單次觸發，現場檢查無廢料、無異物，重置後連續生產正常",
    "misfeed_sensor_fault": "操作員穿新料頭時暫停送料，誤送料偵測器依設計單次觸發（正常保護動作，感測器本身無異常）",
    "die_lubrication_insufficient": "影像判斷噴油嘴無油霧，實為補光燈角度改變；油箱液位與噴油脈衝正常",
    "servo_feeder_drive_alarm": "操作員換料捲時以手動寸動穿料，負載瞬間變化觸發一次位置偏差警報，重置即恢復",
}
FA_HANDLED = {"feed_roller_wear", "material_thickness_dev", "die_misalignment", "slug_pulling", "misfeed_sensor_fault",
              "clutch_brake_wear", "overload_tonnage", "air_pressure_low", "servo_feeder_drive_alarm", "scrap_chute_jam",
              "light_curtain_fault", "central_lube_fault"}
PRESS_SIGNALS = {"tonnage_peak", "tonnage_balance_pct", "die_change", "shut_height_mm", "spm", "stroke_count_since_regrind",
                 "die_stroke_total", "stop_angle_deg", "stop_time_ms", "counterbalance_bar", "lube_pressure_bar",
                 "bearing_temp_c", "die_lube_pulse_per_min", "scrap_conveyor_current_a", "light_curtain_status"}
FEEDER_SIGNALS = {"feed_length_error_mm", "coil_change", "pilot_release_angle_deg", "straightener_setting_mm"}
PRESS_ALARMS = {"DIE_PROTECT", "STRIP_LIFT", "OVERLOAD", "DOUBLE_BLANK", "OVERRUN", "SCRAP_CONV_OVERLOAD", "LC_FAULT",
                "DIE_LUBE_LOW", "CENTRAL_LUBE_LOW"}
FEEDER_ALARMS = {"FD_OVERCURRENT", "FD_POS_DEV", "LOOP_FAULT", "STRIP_EDGE"}
TECH_IDS = [f"TECH-{i:02d}" for i in range(1, 15)]


def _hhmm_of(text: str):
    import re as _re
    m = _re.search(r"(\d{2}):(\d{2})", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def merge_events(recent, sig, recent_x, sig_x, t0):
    """Merge decoy/partner context into the primary case without duplicating the same physical event.

    A coil change or die change can only happen once in the window. If the primary mode already has one,
    the secondary copy (same coil / same die at another time) is dropped together with its
    recent_changes text; a spec change (料厚/材質不同) carried by the secondary event is transferred to
    the primary one. STRIP_EDGE alarms of the secondary mode that would precede the kept coil change are
    dropped too (camber can only come from the coil that is actually loaded). No RNG is consumed.
    """
    sig_x = list(sig_x)
    recent_x = list(recent_x)
    for kind, label in (("coil_change", "換料捲"), ("die_change", "換模")):
        prim = [s for s in sig if s["signal"] == kind]
        sec = [s for s in sig_x if s["signal"] == kind]
        if not (prim and sec):
            continue
        keep = prim[0]
        spec_text = None
        for s in sec:
            if kind == "coil_change" and "spec_changed" in str(s["value"]) and "spec_changed" not in str(keep["value"]):
                keep["value"] = f"{keep['value']} spec_changed"
                spec_text = next((r[r.index("（") + 1:-1] for r in recent_x if r.startswith(label) and "→" in r), None)
            sig_x.remove(s)
        recent_x = [r for r in recent_x if not r.startswith(label)]
        if spec_text:
            recent = [f"{r[:-1]}，{spec_text}）" if r.startswith(label) and r.endswith("）") else r for r in recent]
        if kind == "coil_change":
            sig_x = [s for s in sig_x if not (s["signal"] == "alarm_code" and s["value"] == "STRIP_EDGE" and s["t"] < keep["t"])]
    return recent + recent_x, sorted(sig + sig_x, key=lambda x: x["t"])


def add_missing_change_signals(recent, sig, t0):
    """recent_changes says 「換料捲 HH:MM」/「換模 HH:MM」 but the log has no such event → add it (no RNG)."""
    out = list(sig)
    for kind, label in (("coil_change", "換料捲"), ("die_change", "換模")):
        if any(s["signal"] == kind for s in out):
            continue
        for r in recent:
            if r.startswith(label) and _hhmm_of(r):
                h, m = _hhmm_of(r)
                t = t0.replace(hour=h, minute=m, second=0)
                if t > t0:
                    t -= dt.timedelta(days=1)
                val = r[r.index("（") + 1:-1] if "（" in r else "unknown"
                out.append({"t": t.isoformat(), "signal": kind, "value": val})
                break
    return sorted(out, key=lambda x: x["t"])


class Planner:
    def __init__(self, seed: int = 2026):
        self.r = random.Random(seed)
        self.cat = catalog()
        self.busy = defaultdict(list)  # machine -> [(start, end)]
        self._esc = 0
        self.pm_offset = {m: self.r.randint(0, PM_CYCLE - 1) for m in MACHINES}

    # ---------- calendar
    def days_since_pm(self, machine: str, d: dt.date) -> int:
        base = (d - dt.date(2025, 8, 1)).days + self.pm_offset[machine]
        return base % PM_CYCLE

    def pick_time(self, machine: str, fm_id: str, need_pm=None, summer=False):
        for _ in range(500):
            if summer and self.r.random() < 0.6:
                d = dt.date(2026, self.r.choice([6, 7, 8, 9]), self.r.randint(1, 28))
            else:
                d = START + dt.timedelta(days=self.r.randint(0, (END - START).days))
            if d.weekday() == 6 and self.r.random() < 0.8:  # Sundays mostly off
                continue
            dsp = self.days_since_pm(machine, d)
            if need_pm and not need_pm(dsp):
                continue
            shift = self.r.choices(["day", "swing", "night"], [0.30, 0.30, 0.40])[0]
            h0 = {"day": 8, "swing": 16, "night": 0}[shift]
            t = dt.datetime(d.year, d.month, d.day, h0, 0, tzinfo=TZ) + dt.timedelta(
                minutes=self.r.randint(25, 7 * 60 + 20), seconds=self.r.randint(0, 59))
            if any(abs((t - s).total_seconds()) < 8 * 3600 for s, _ in self.busy[machine]):
                continue  # keep cases on the same machine well apart → no contradictions
            return t, shift, dsp
        raise RuntimeError("no slot")

    # ---------- plan
    def plan(self) -> list[dict]:
        rot = ["typical", "composite", "false_alarm", "escalated_or_new"]
        rot_i = 0
        plans = []
        for fm_id, fm in self.cat.items():
            if fm_id in HIGH_FREQ:
                types = ["typical"] * 4 + ["wrong_first"] * 2 + ["composite", "false_alarm", "escalated_or_new"]
            else:
                types = ["typical"] * 2 + ["wrong_first"]
                for _ in range(2):
                    types.append(rot[rot_i % 4])
                    rot_i += 1
            for ct in types:
                if ct == "escalated_or_new":
                    ct = "new" if self._esc % 2 == 0 else "escalated"
                    self._esc += 1
                if not fm["distinguish_from"] and ct in ("wrong_first", "composite"):
                    ct = "typical"
                plans.append({"fm_id": fm_id, "case_type": ct})
        return plans

    def pick_machine(self, fm_id: str) -> str:
        types = self.cat[fm_id]["machine_types"]
        cands = [m for m, v in MACHINES.items() if v["type"] in types]
        w = []
        for m in cands:
            wt = 0.65 if "-L1-" in m else 0.35
            if fm_id == "clutch_brake_wear":
                wt = 0.75 if MACHINES[m]["type"] == "mechanical_power_press" else 0.25
            if fm_id in ("material_thickness_dev", "coil_camber", "misfeed_sensor_fault") and MACHINES[m]["type"] == "servo_roll_feeder":
                wt *= 1.3
            w.append(wt)
        return self.r.choices(cands, w)[0]

    def build(self, idx: int, p: dict) -> dict:
        r, fm_id, ct = self.r, p["fm_id"], p["case_type"]
        fm = self.cat[fm_id]
        machine = self.pick_machine(fm_id)
        mtype = MACHINES[machine]["type"]
        line = "L1" if "-L1-" in machine else "L2"
        press = "STP-L1-PR01" if line == "L1" else "STP-L2-PR03"
        need_pm = None
        if fm_id == "feed_roller_wear" and ct != "false_alarm":
            need_pm = lambda x: x > 30
        if fm_id == "central_lube_fault":
            need_pm = lambda x: x > 20
        summer = fm_id in ("central_lube_fault", "servo_feeder_drive_alarm", "die_lubrication_insufficient")
        opened, shift, dsp = self.pick_time(machine, fm_id, need_pm, summer)
        die_id = r.choice(DIES_BY_LINE[line])
        die = DIES[die_id]
        coil_id = f"C-{opened:%y%m}-{r.randint(101, 399)}"
        material = {"grade": die["grade"], "thickness_mm": die["t"], "coil_id": coil_id}

        wrong = None
        partner = None
        if ct == "wrong_first":
            wrong = r.choice(fm["distinguish_from"])
        if ct == "composite":
            partner = r.choice(fm["distinguish_from"])
        recent, sig = self.signals(fm_id, ct, opened, die, die_id, material, machine, press, mtype)
        if wrong:  # add the misleading context that made the wrong hypothesis plausible
            recent_w, sig_w = self.signals(wrong, "decoy", opened, die, die_id, material, machine, press, mtype)
            recent, sig = merge_events(recent, sig, recent_w, sig_w, opened)
        if partner:
            recent_p, sig_p = self.signals(partner, "typical", opened, die, die_id, material, machine, press, mtype)
            recent, sig = merge_events(recent, sig, recent_p, sig_p, opened)
        recent = list(dict.fromkeys(recent))
        sig = add_missing_change_signals(recent, sig, opened)
        feeder = "STP-L1-FD02" if line == "L1" else "STP-L2-FD04"
        seen, clean = set(), []
        for x in sorted(sig, key=lambda x: x["t"]):
            if "machine_id" not in x:
                if mtype == "servo_roll_feeder" and (x["signal"] in PRESS_SIGNALS or (x["signal"] == "alarm_code" and x["value"] in PRESS_ALARMS)):
                    x["machine_id"] = press
                elif mtype != "servo_roll_feeder" and (x["signal"] in FEEDER_SIGNALS or x["signal"] == "param_change" and "pilot" in str(x["value"]) or (x["signal"] == "alarm_code" and x["value"] in FEEDER_ALARMS)):
                    x["machine_id"] = feeder
            key = (x["t"][:16], x["signal"], str(x["value"]), x.get("machine_id"))
            if key not in seen:
                seen.add(key)
                clean.append(x)
        sig = clean

        # first hypothesis
        by = ["jev", "technician", "rule"][idx % 3]
        if ct == "wrong_first":
            first = {"failure_mode_id": wrong, "by": by, "p": round(r.uniform(0.36, 0.58), 2)}
        elif ct == "composite":
            fid = r.choice([fm_id, partner])
            first = {"failure_mode_id": fid, "by": by, "p": round(r.uniform(0.40, 0.62), 2)}
        elif ct == "new":
            first = {"failure_mode_id": fm_id, "by": "jev" if r.random() < 0.6 else by, "p": round(r.uniform(0.28, 0.45), 2)}
        else:
            first = {"failure_mode_id": fm_id, "by": by, "p": round(r.uniform(0.55, 0.91), 2)}
        if first["by"] != "jev":
            first["p"] = None

        new_mode = None
        if ct == "new":
            name, desc = r.choice(NEW_CANDIDATES[FAMILY[fm_id]])
            name, desc = NEW_MODE_OVERRIDE.get(fm_id, (name, desc))
            new_mode = {"proposed_id": name, "description": desc}
        final = {"false_alarm": "none", "new": "new"}.get(ct, fm_id)
        outcome = {"false_alarm": "false_alarm", "new": "escalated", "escalated": "escalated"}.get(ct, "resolved")
        lo, hi = DOWNTIME[fm_id]
        dmin = r.randint(lo, hi)
        if ct == "wrong_first":
            dmin = int(dmin * r.uniform(1.3, 1.7))
        elif ct == "composite":
            dmin = int(dmin * r.uniform(1.2, 1.5))
        elif ct == "false_alarm":
            dmin = r.randint(5, 22)
        elif ct in ("escalated", "new"):
            dmin = r.randint(max(150, hi), 720)
        closed = opened + dt.timedelta(minutes=dmin, seconds=r.randint(0, 300))
        self.busy[machine].append((opened, closed))

        src, evt, sev = TRIGGER[fm_id]
        if ct == "false_alarm":
            src = r.choice(["vision", "fused", "mes"])
        if fm_id == "light_curtain_fault" and any(s["signal"] == "person_in_zone" for s in sig):
            sev, evt, src = "S3", "person_in_danger_zone", "fused"
        if fm_id == "air_pressure_low" and ct != "false_alarm" and any(
                s.get("machine_id") and s["signal"] == "air_pressure_bar" for s in sig):
            sev = "S3"  # only when another machine on the same supply line also dropped (severity_note)
        sop_used = []
        if first["failure_mode_id"] in self.cat and first["failure_mode_id"] != final:
            sop_used.append(self.cat[first["failure_mode_id"]]["sop"])
        if final in self.cat:
            sop_used.append(self.cat[final]["sop"])
        if partner:
            sop_used.append(self.cat[partner]["sop"])
        if ct == "false_alarm":
            sop_used = [fm["sop"]]
        sop_used = list(dict.fromkeys(sop_used))
        entered = ct != "false_alarm" or r.random() < 0.3
        near_miss = fm_id == "light_curtain_fault" and sev == "S3"
        level = r.choices(["L1", "L2", "L3"], [0.3, 0.5, 0.2])[0]
        if fm["category"] == "safety" or ct in ("escalated", "new"):
            level = r.choice(["L2", "L3"])
        out = {
            "idx": idx, "fm_id": fm_id, "case_type": ct, "machine_id": machine, "machine_type": mtype,
            "opened_at": opened.isoformat(), "closed_at": closed.isoformat(), "shift": shift,
            "trigger": {"source": src, "event_type": evt, "rule_severity": sev},
            "context": {"days_since_pm": dsp, "recent_changes": recent, "material": material,
                        "die_id": die_id if mtype != "servo_roll_feeder" or fm_id in ("pilot_release_timing", "material_thickness_dev") else die_id,
                        "part": die["part"], "season": "summer" if opened.month in (6, 7, 8, 9) else "other"},
            "log_signals": sig, "first_hypothesis": first, "final_root_cause": final,
            "contributing_causes": [partner] if partner else [], "hypothesis_correct": first["failure_mode_id"] == final,
            "new_mode": new_mode, "outcome": outcome, "downtime_min": dmin, "sop_used": sop_used,
            "safety_flags": {"loto_done": entered and fm["loto_required"], "danger_zone_entry": entered, "near_miss": near_miss},
            "technician": {"id": r.choice(TECH_IDS), "level": level},
            "false_alarm_story": r.choice(FALSE_ALARM_STORY[FAMILY[fm_id]]) if ct == "false_alarm" else None,
            "parts_vocab": PARTS[fm_id] if ct not in ("false_alarm", "new") else [],
            "escalated_to": r.choice(["模具廠", "模具技師", "設備工程師"] if FAMILY[fm_id] == "die" else ["設備工程師", "原廠技師", "原廠技師"]) if ct in ("escalated", "new") else None,
        }
        if ct == "false_alarm" and fm_id in FA_STORY_OVERRIDE:
            out["false_alarm_story"] = FA_STORY_OVERRIDE[fm_id]
        return out

    # ---------- signals (shape follows failure_modes/stamping.yaml log_signals)
    def signals(self, fm_id, ct, t0, die, die_id, material, machine, press, mtype):
        r = self.r
        decoy = ct == "decoy"
        fa = ct == "false_alarm"

        def at(mins, sig, val, mid=None):
            d = {"t": (t0 + dt.timedelta(minutes=mins, seconds=r.randint(0, 50) if mins else 0)).isoformat(),
                 "signal": sig, "value": val}
            if mid:
                d["machine_id"] = mid
            return d

        def hhmm(mins):
            return (t0 + dt.timedelta(minutes=mins)).strftime("%H:%M")

        recent, s = [], []
        ton = die["ton"]
        if fa and fm_id not in FA_HANDLED:
            return [], [at(0, "state", "alarm"), at(1, "state", "running")]
        if fm_id == "feed_roller_wear":
            if decoy:
                return [], [at(-r.randint(8, 14), "feed_length_error_mm", round(r.uniform(0.04, 0.06), 2))]
            if fa:
                return [], [at(-6, "feed_length_error_mm", 0.02), at(0, "feed_rate", 0)]
            a = r.uniform(0.17, 0.26)
            s += [at(-14, "feed_length_error_mm", round(r.uniform(0.02, 0.04), 2)), at(-9, "feed_length_error_mm", round(a * 0.4, 2)),
                  at(-4, "feed_length_error_mm", round(a * 0.7, 2)), at(0, "feed_length_error_mm", round(a, 2)),
                  at(0, "alarm_code", "MISFEED"), at(0, "feed_rate", 0)]
        elif fm_id == "material_thickness_dev":
            m = r.randint(5, 26)
            recent.append(f"換料捲 {hhmm(-m)}（{material['coil_id']}）")
            if decoy:
                return recent, [at(-m, "coil_change", material["coil_id"])]
            if fa:
                return recent, [at(-m, "coil_change", material["coil_id"]), at(-2, "tonnage_peak", ton + r.randint(-2, 2))]
            sign = r.choice([1, 1, -1])
            k = r.uniform(0.06, 0.11)
            s += [at(-m - 3, "tonnage_peak", ton), at(-m, "coil_change", material["coil_id"]),
                  at(-m + 2, "tonnage_peak", round(ton * (1 + sign * k))), at(-2, "tonnage_peak", round(ton * (1 + sign * k)) + r.randint(-1, 1)),
                  at(-m + 2, "feed_length_error_mm", round(r.uniform(0.07, 0.11), 2)), at(0, "alarm_code", "MISFEED")]
            if mtype == "servo_roll_feeder":
                for x in s:
                    if x["signal"] == "tonnage_peak":
                        x["machine_id"] = press
        elif fm_id == "die_misalignment":
            m = r.randint(15, 95)
            recent.append(f"換模 {hhmm(-m)}（{die_id}）")
            if decoy:
                return recent, [at(-m, "die_change", die_id)]
            if fa:
                return recent, [at(-m, "die_change", die_id), at(-3, "tonnage_balance_pct", r.randint(2, 4))]
            s += [at(-m, "die_change", die_id), at(-m + 4, "shut_height_mm", round(die["shut"] + r.choice([0.0, 0.01, -0.01]), 2)),
                  at(-m + 6, "tonnage_balance_pct", r.randint(11, 18)), at(-1, "tonnage_balance_pct", r.randint(12, 19))]
        elif fm_id == "pilot_release_timing":
            m = r.randint(25, 110)
            if r.random() < 0.5:
                recent.append(f"沖速由 32 調為 40 spm {hhmm(-m)}")
                ch = [at(-m, "param_change", "spm 32→40"), at(-m, "spm", 40)]
            else:
                old = die["pilot"]
                recent.append(f"導正釋放角度由 {old}° 改為 {old - 15}° {hhmm(-m)}")
                ch = [at(-m, "param_change", f"pilot_release_angle_deg {old}→{old - 15}"), at(-m, "pilot_release_angle_deg", old - 15)]
            if decoy:
                return recent, ch[:1]
            s += ch + [at(-5, "feed_length_error_mm", round(r.uniform(0.01, 0.02), 2)), at(0, "feed_length_error_mm", round(r.uniform(0.01, 0.03), 2))]
        elif fm_id == "coil_camber":
            m = r.randint(40, 200)
            recent.append(f"換料捲 {hhmm(-m)}（{material['coil_id']}）")
            if decoy:
                return recent, [at(-r.randint(3, 10), "alarm_code", "STRIP_EDGE")]
            s += [at(-m, "coil_change", material["coil_id"]), at(-r.randint(20, 35), "alarm_code", "STRIP_EDGE"),
                  at(-r.randint(6, 15), "alarm_code", "STRIP_EDGE"), at(0, "alarm_code", "STRIP_EDGE"),
                  at(-2, "feed_length_error_mm", round(r.uniform(0.01, 0.03), 2))]
        elif fm_id == "slug_pulling":
            m = r.randint(15, 55)
            recent.append(f"提速 32→40 spm {hhmm(-m)}")
            if decoy:
                return recent, [at(-m, "spm", 40)]
            if fa:
                return recent, [at(-m, "spm", 40), at(0, "alarm_code", "DIE_PROTECT"), at(1, "state", "running")]
            s += [at(-m - 5, "spm", 32), at(-m, "spm", 40), at(-r.randint(8, 14), "alarm_code", "DIE_PROTECT"),
                  at(0, "alarm_code", "DIE_PROTECT"), at(-3, "feed_length_error_mm", round(r.uniform(0.01, 0.02), 2))]
        elif fm_id == "punch_edge_wear":
            if decoy:
                return [], [at(-1, "stroke_count_since_regrind", r.randint(70000, 78000))]
            cnt = r.randint(86000, 112000)
            s += [at(-60 * 24 * 6, "tonnage_peak", ton), at(-60 * 24 * 3, "tonnage_peak", round(ton * 1.03)),
                  at(-1, "tonnage_peak", round(ton * r.uniform(1.05, 1.08))), at(-1, "stroke_count_since_regrind", cnt),
                  at(-1, "tonnage_balance_pct", r.randint(2, 4))]
        elif fm_id == "die_lubrication_insufficient":
            if decoy:
                return ["品質站回報成形面有刮痕"], []
            s += [at(-40, "die_lube_pulse_per_min", 6), at(-15, "die_lube_pulse_per_min", round(r.uniform(2.5, 3.5), 1)),
                  at(-2, "die_lube_pulse_per_min", round(r.uniform(0.5, 1.8), 1)), at(0, "alarm_code", "DIE_LUBE_LOW"),
                  at(0, "stroke_count_since_regrind", r.randint(18000, 65000))]
        elif fm_id == "misfeed_sensor_fault":
            if decoy:
                return [], [at(-r.randint(30, 90), "alarm_code", "MISFEED"), at(-r.randint(29, 30), "state", "running")]
            if fa:
                return [], [at(0, "alarm_code", "MISFEED"), at(1, "state", "running"), at(-1, "feed_length_error_mm", 0.01)]
            n = r.randint(2, 4)
            for k in range(n, 0, -1):
                mm = -k * r.randint(25, 60)
                s += [at(mm, "alarm_code", "MISFEED"), at(mm + 1, "state", "running")]
            s += [at(0, "alarm_code", "MISFEED"), at(-1, "feed_length_error_mm", round(r.uniform(0.01, 0.02), 2))]
        elif fm_id == "stripper_spring_fatigue":
            if decoy:
                return [], [at(-r.randint(5, 20), "alarm_code", "STRIP_LIFT")]
            s += [at(-70, "die_stroke_total", r.randint(520000, 820000)), at(-35, "alarm_code", "STRIP_LIFT"),
                  at(-30, "spm", 30), at(-8, "alarm_code", "STRIP_LIFT"), at(0, "alarm_code", "STRIP_LIFT")]
        elif fm_id == "clutch_brake_wear":
            if decoy:
                return [], [at(-1, "stop_angle_deg", round(r.uniform(12.5, 13.8), 1))]
            if fa:
                return [], [at(0, "stop_angle_deg", 15.4), at(2, "stop_angle_deg", 10.8), at(0, "air_pressure_bar", 5.7)]
            s += [at(-60 * 24 * 3, "stop_angle_deg", round(r.uniform(10.5, 11.5), 1)), at(-60 * 24, "stop_angle_deg", round(r.uniform(13.0, 14.2), 1)),
                  at(0, "stop_angle_deg", round(r.uniform(15.6, 17.5), 1)), at(0, "stop_time_ms", r.randint(236, 262)),
                  at(0, "alarm_code", "OVERRUN"), at(0, "air_pressure_bar", round(r.uniform(5.6, 5.9), 1))]
        elif fm_id == "overload_tonnage":
            lim = MACHINES[press]["tonnage_limit"]
            if decoy:
                return [], [at(-1, "tonnage_peak", lim + r.randint(3, 10))]
            if fa:
                return [], [at(0, "tonnage_peak", lim + 2), at(0, "alarm_code", "OVERLOAD")]
            s += [at(-1, "tonnage_peak", ton + r.randint(-3, 3)), at(0, "tonnage_peak", lim + r.randint(8, 30)),
                  at(0, "alarm_code", "OVERLOAD")]
            if r.random() < 0.6:
                s.append(at(0, "alarm_code", "DOUBLE_BLANK"))
        elif fm_id == "air_pressure_low":
            if decoy:
                return [], [at(-2, "air_pressure_bar", round(r.uniform(5.0, 5.2), 1))]
            if fa:
                return [], [at(0, "air_pressure_bar", 4.7), at(1, "air_pressure_bar", 5.6)]
            other = [m for m in MACHINES if m != machine and MACHINES[m]["type"] != "servo_roll_feeder"][0]
            s += [at(-12, "air_pressure_bar", round(r.uniform(5.5, 5.8), 1)), at(-3, "air_pressure_bar", round(r.uniform(4.9, 5.2), 1)),
                  at(0, "air_pressure_bar", round(r.uniform(4.2, 4.7), 1)), at(0, "alarm_code", "AIR_LOW")]
            if r.random() < 0.6:
                s.append(at(0, "air_pressure_bar", round(r.uniform(4.3, 4.7), 1), mid=other))
        elif fm_id == "counterbalance_misset":
            m = r.randint(30, 200)
            recent.append(f"換模 {hhmm(-m)}（{die_id}）")
            if decoy:
                return recent, [at(-m, "die_change", die_id)]
            cur = 105 if mtype == "servo_press" else 95
            s += [at(-m, "die_change", die_id), at(-m + 3, "counterbalance_bar", round(die["cb"] - r.choice([0.4, 0.5, 0.6, -0.4]), 1)),
                  at(-m - 10, "motor_current_a", cur), at(-2, "motor_current_a", cur + r.randint(14, 26)),
                  at(-2, "air_pressure_bar", round(r.uniform(5.6, 5.9), 1))]
        elif fm_id == "servo_feeder_drive_alarm":
            if decoy:
                return [], [at(-3, "motor_temp_c", r.randint(68, 72))]
            if fa:
                return [], [at(0, "alarm_code", "FD_POS_DEV"), at(1, "state", "running")]
            s += [at(-5, "feed_length_error_mm", round(r.uniform(0.01, 0.02), 2)), at(-3, "motor_temp_c", r.randint(76, 84)),
                  at(0, "motor_current_a", round(r.uniform(12.5, 16.0), 1)), at(0, "alarm_code", r.choice(["FD_OVERCURRENT", "FD_POS_DEV"])),
                  at(0, "feed_rate", 0)]
        elif fm_id == "straightener_misset":
            m = r.randint(5, 50)
            prev = r.choice([("SECC", 0.8), ("SGCC", 1.6), ("SECC", 1.2)])
            recent.append(f"換料捲 {hhmm(-m)}（{prev[0]} {prev[1]} mm → {material['grade']} {material['thickness_mm']} mm）")
            if decoy:
                return recent, [at(-m, "coil_change", f"{material['coil_id']} spec_changed")]
            s += [at(-m, "coil_change", f"{material['coil_id']} spec_changed"), at(-m + 1, "straightener_setting_mm", round(1.2 * prev[1], 2)),
                  at(-2, "alarm_code", "LOOP_FAULT"), at(0, "alarm_code", "MISFEED")]
        elif fm_id == "scrap_chute_jam":
            if decoy:
                return [], [at(-r.randint(4, 12), "alarm_code", "DIE_PROTECT")]
            if fa:
                return [], [at(0, "alarm_code", "DIE_PROTECT"), at(-1, "scrap_conveyor_current_a", 1.2)]
            v = r.choice([round(r.uniform(2.3, 3.0), 1), 0.0])
            s += [at(-20, "scrap_conveyor_current_a", 1.2), at(-4, "scrap_conveyor_current_a", v),
                  at(-1, "alarm_code", "SCRAP_CONV_OVERLOAD" if v else "DIE_PROTECT"), at(0, "alarm_code", "DIE_PROTECT")]
        elif fm_id == "light_curtain_fault":
            if fa:
                return [], [at(0, "person_in_zone", True), at(0, "light_curtain_status", "OK")]
            if r.random() < 0.45:
                s += [at(-15, "light_curtain_status", "MUTED"), at(-1, "person_in_zone", True)]
            else:
                s += [at(-25, "alarm_code", "LC_FAULT"), at(-10, "alarm_code", "LC_FAULT"), at(0, "light_curtain_status", "FAULT"), at(0, "alarm_code", "LC_FAULT")]
        elif fm_id == "shut_height_misset":
            m = r.randint(8, 50)
            recent.append(f"換模 {hhmm(-m)}（{die_id}）")
            if decoy:
                return recent, [at(-m, "die_change", die_id)]
            diff = r.choice([0.25, 0.3, 0.4, -0.3, -0.5])
            s += [at(-m, "die_change", die_id), at(-m + 3, "shut_height_mm", round(die["shut"] + diff, 2)),
                  at(-m + 5, "tonnage_peak", round(ton * (1.15 if diff < 0 else 0.85))), at(-m + 5, "tonnage_balance_pct", r.randint(2, 4))]
        elif fm_id == "central_lube_fault":
            if decoy:
                return [], [at(-30, "bearing_temp_c", r.randint(50, 54))]
            if fa:
                return [], [at(0, "alarm_code", "CENTRAL_LUBE_LOW"), at(2, "lube_pressure_bar", 2.0), at(0, "bearing_temp_c", 42)]
            s += [at(-200, "bearing_temp_c", r.randint(39, 43)), at(-120, "lube_pressure_bar", round(r.uniform(0.6, 0.9), 1)),
                  at(-90, "bearing_temp_c", r.randint(50, 55)), at(0, "bearing_temp_c", r.randint(61, 68)),
                  at(0, "alarm_code", "CENTRAL_LUBE_LOW")]
        if fa and not s:
            s = [at(0, "state", "alarm"), at(1, "state", "running")]
        return recent, sorted(s, key=lambda x: x["t"])
