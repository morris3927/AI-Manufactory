"""Deterministic skeletons for synthetic welding / assembly repair cases (07_RAG知識庫資料.md §6.2).

Same principle as case_skeleton.py (stamping, untouched): the PROGRAM decides every fact
(root cause, case type, wrong first hypothesis drawn from distinguish_from, machine, time / shift,
PM calendar, recent events, log-signal shapes, trigger, downtime, safety flags); the LLM only writes text.
60 cases per line (10 modes × 6): typical 31, wrong_first 11 (18%), composite 6, false_alarm 6,
escalated 3, new 3; held-out = 1 per mode (10) (模擬資料).
"""
from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict

from common import catalog
from lines import LINES

TZ = dt.timezone(dt.timedelta(hours=8))
START, END = dt.date(2025, 10, 1), dt.date(2026, 9, 30)
SEED = {"welding": 2027, "assembly": 2028}

PARTS = {
    "welding": {
        "wire_feed_birdnest": ["wire_liner", "drive_roll_set", "wire_inlet_guide"],
        "contact_tip_wear": ["contact_tip", "tip_holder"],
        "shielding_gas_low": ["gas_hose", "gas_fitting", "gas_regulator"],
        "nozzle_spatter_buildup": ["nozzle", "gas_diffuser", "cleaning_station_cutter"],
        "tcp_drift": ["torch_neck", "torch_mount_insulator", "collision_sensor"],
        "ground_poor": ["ground_clamp", "return_cable_lug", "return_cable"],
        "fixture_fitup_gap": ["locating_pin", "clamp_jaw", "clamp_spring", "fixture_shim"],
        "spot_electrode_wear": ["electrode_cap", "dresser_blade"],
        "cooling_water_low": ["coolant_filter", "coolant_hose", "coolant_fitting"],
        "fume_extraction_fault": ["extraction_filter_cartridge", "exhaust_fan_belt"],
    },
    "assembly": {
        "torque_not_reached": ["screw_m3"],
        "cross_thread_strip": ["pem_nut", "screw_m3"],
        "bit_wear_camout": ["driver_bit", "screw_m3"],
        "screw_feeder_jam": ["feeder_rail_guide", "escapement_pin", "pickup_nozzle"],
        "wrong_screw_type": ["screw_m3"],
        "missing_screw": ["screw_m3"],
        "tool_calibration_due": ["calibrated_spare_screwdriver", "clutch_spring"],
        "cable_pinch_misroute": ["cable_harness", "edge_grommet", "cable_tie"],
        "connector_not_seated": ["cable_harness"],
        "ppe_esd_missing": [],
    },
}
FAMILY = {
    "wire_feed_birdnest": "arc", "contact_tip_wear": "arc", "shielding_gas_low": "arc", "nozzle_spatter_buildup": "arc",
    "ground_poor": "arc", "tcp_drift": "robot", "fixture_fitup_gap": "robot", "spot_electrode_wear": "spot",
    "cooling_water_low": "spot", "fume_extraction_fault": "fume",
    "torque_not_reached": "screw", "cross_thread_strip": "screw", "bit_wear_camout": "screw", "screw_feeder_jam": "screw",
    "wrong_screw_type": "screw", "missing_screw": "screw", "tool_calibration_due": "screw",
    "cable_pinch_misroute": "cable", "connector_not_seated": "cable", "ppe_esd_missing": "ppe",
}
NEW_CANDIDATES = {
    "arc": [("wire_surface_rust", "銲絲批次表面生鏽、銅鍍層剝落，送絲阻力與起弧都不穩（送絲機與導電嘴本身正常）"),
            ("gas_solenoid_sticking", "保護氣電磁閥間歇卡滯，起弧瞬間保護氣延遲送出"),
            ("positioner_slip_ring_wear", "變位機回路滑環碳刷磨耗，轉台轉到某些角度時回路電流不穩")],
    "robot": [("program_point_edited", "教導程式中一個點位被誤改，只有該銲道偏移"),
              ("positioner_index_error", "變位機分度定位銷磨耗，轉台停止角度偏移")],
    "spot": [("transformer_overheat_derate", "點銲變壓器冷卻風扇故障，連續生產時過熱降載"),
             ("coolant_pump_impeller_wear", "冷卻水泵浦葉輪磨耗，流量在高溫時下降")],
    "fume": [("duct_damper_closed", "排風管手動風門在清掃後被誤關一半"),
             ("fan_motor_bearing_wear", "排風機馬達軸承磨耗，風量逐步下降")],
    "screw": [("wrong_program_selected", "換線後控制器選到另一機種的鎖附程式（扭力、角度窗不同）"),
              ("pem_nut_supplier_lot", "壓鉚螺母供應商批次牙規偏緊，旋入扭力普遍偏高"),
              ("vacuum_pickup_leak", "吸嘴真空管龜裂漏氣，螺絲在下降途中掉落")],
    "cable": [("test_fixture_pin_wear", "測試治具彈簧探針磨耗，偵測時有時無（機殼本身正常）"),
              ("harness_crimp_defect", "線束供應商壓接不良，端子在連接器內退針")],
    "ppe": [("esd_tester_fault", "靜電手環測試站接地線斷，所有人都測不過")],
}
FALSE_ALARM_STORY = {
    "arc": ["銲接工位 ROI 內的正常火花被影像誤判為異常", "感測值單點跳動（雜訊），持續 2 秒即恢復，現場檢查正常",
            "品質站把首件的定位點銲誤判為氣孔"],
    "robot": ["TCP 校驗尖點上沾了一點銲渣造成讀值偏差，清潔後重測正常", "品質站量測治具歸零錯誤，誤判銲道偏移"],
    "spot": ["剝離試驗試片取錯（取到修磨前的試片）", "冷卻水流量計接頭有氣泡造成讀值短暫偏低"],
    "fume": ["影像把銲接工位 ROI 內的正常銲煙與火花判成工位外煙霧（ROI 邊界設定過寬）", "排氣風量計在濾筒自動脈衝清灰時短暫下降"],
    "screw": ["作業員把工件放歪觸發 NOK，重新放正後鎖附正常", "控制器與 MES 時間不同步，NOK 紀錄對到錯的工件",
              "補料時料盤短暫空料觸發警報，補完即正常"],
    "cable": ["測試站治具線鬆脫，重插治具線後機殼測試通過", "影像把新機種不同顏色的線材判成走線錯誤"],
    "ppe": ["影像把膚色手套判成未戴手套", "作業員剛到站還沒開始作業就被拍到"],
}
DOWNTIME = {
    "wire_feed_birdnest": (25, 60), "contact_tip_wear": (10, 30), "shielding_gas_low": (20, 60),
    "nozzle_spatter_buildup": (15, 40), "tcp_drift": (60, 180), "ground_poor": (30, 90), "fixture_fitup_gap": (60, 200),
    "spot_electrode_wear": (15, 45), "cooling_water_low": (40, 150), "fume_extraction_fault": (60, 240),
    "torque_not_reached": (15, 45), "cross_thread_strip": (20, 60), "bit_wear_camout": (10, 25), "screw_feeder_jam": (10, 35),
    "wrong_screw_type": (30, 90), "missing_screw": (15, 50), "tool_calibration_due": (30, 90),
    "cable_pinch_misroute": (20, 60), "connector_not_seated": (10, 30), "ppe_esd_missing": (5, 15),
}
TRIGGER = {
    "wire_feed_birdnest": ("mes", "wire_feed_alarm", "S2"), "contact_tip_wear": ("rule", "arc_start_trend", "S2"),
    "shielding_gas_low": ("mes", "gas_low_alarm", "S2"), "nozzle_spatter_buildup": ("rule", "quality_trend", "S2"),
    "tcp_drift": ("mes", "quality_defect", "S2"), "ground_poor": ("rule", "arc_unstable", "S2"),
    "fixture_fitup_gap": ("mes", "quality_defect", "S2"), "spot_electrode_wear": ("rule", "quality_defect", "S2"),
    "cooling_water_low": ("mes", "coolant_alarm", "S2"), "fume_extraction_fault": ("fused", "exhaust_abnormal", "S2"),
    "torque_not_reached": ("mes", "screw_nok", "S2"), "cross_thread_strip": ("mes", "screw_nok", "S2"),
    "bit_wear_camout": ("rule", "nok_rate_trend", "S2"), "screw_feeder_jam": ("mes", "feeder_timeout", "S2"),
    "wrong_screw_type": ("mes", "screw_nok", "S2"), "missing_screw": ("fused", "screw_count_mismatch", "S2"),
    "tool_calibration_due": ("rule", "calibration_overdue", "S1"), "cable_pinch_misroute": ("mes", "test_fail", "S2"),
    "connector_not_seated": ("mes", "test_fail", "S2"), "ppe_esd_missing": ("vision", "ppe_violation", "S2"),
}
PARTS_MADE = {"welding": ["1U 機殼後框支架", "2U 機殼側框", "硬碟籠框架", "電源支架"],
              "assembly": ["1U 機殼組立", "2U 機殼組立", "4U 機殼組立"]}
FIXTURES = ["FX-A", "FX-B", "FX-C", "FX-D"]
TECH_IDS = {"welding": [f"TECH-{i:02d}" for i in range(21, 31)], "assembly": [f"TECH-{i:02d}" for i in range(41, 51)]}


class Planner:
    def __init__(self, line: str):
        self.line = line
        self.cfg = LINES[line]
        self.r = random.Random(SEED[line])
        self.cat = catalog(line)
        self.machines = self.cfg["machines"]
        self.pm = self.cfg["pm_cycle"]
        self.busy = defaultdict(list)
        self._esc = 0
        self.pm_offset = {m: self.r.randint(0, self.pm - 1) for m in self.machines}

    def days_since_pm(self, machine, d):
        return ((d - dt.date(2025, 8, 1)).days + self.pm_offset[machine]) % self.pm

    def pick_time(self, machine, summer=False):
        r = self.r
        for _ in range(500):
            if summer and r.random() < 0.6:
                d = dt.date(2026, r.choice([6, 7, 8, 9]), r.randint(1, 28))
            else:
                d = START + dt.timedelta(days=r.randint(0, (END - START).days))
            if d.weekday() == 6 and r.random() < 0.8:
                continue
            shift = r.choices(["day", "swing", "night"], [0.35, 0.35, 0.30])[0]
            h0 = {"day": 8, "swing": 16, "night": 0}[shift]
            t = dt.datetime(d.year, d.month, d.day, h0, 0, tzinfo=TZ) + dt.timedelta(
                minutes=r.randint(25, 7 * 60 + 20), seconds=r.randint(0, 59))
            if any(abs((t - s).total_seconds()) < 8 * 3600 for s, _ in self.busy[machine]):
                continue
            return t, shift, self.days_since_pm(machine, d)
        raise RuntimeError("no slot")

    def plan(self):
        rot = ["composite", "false_alarm", "escalated_or_new"] * 6
        rot[7], rot[12] = "wrong_first", "wrong_first"   # 2 extra wrong-first slots on two different modes
        rot[11] = "composite"
        rot = rot[:18]
        plans, k = [], 0
        for fm_id, fm in self.cat.items():
            if fm["safety_only_sop"]:
                types = ["typical"] * 4 + ["false_alarm", "escalated_or_new"]
            else:
                types = ["typical"] * 3 + ["wrong_first", rot[k], rot[k + 1]]
                k += 2
            for ct in types:
                if ct == "escalated_or_new":
                    ct = "new" if self._esc % 2 == 0 else "escalated"
                    self._esc += 1
                plans.append({"fm_id": fm_id, "case_type": ct})
        return plans

    def build(self, idx, p):
        r, fm_id, ct = self.r, p["fm_id"], p["case_type"]
        fm = self.cat[fm_id]
        cands = [m for m, v in self.machines.items() if v["type"] in fm["machine_types"]]
        machine = r.choice(cands)
        mtype = self.machines[machine]["type"]
        opened, shift, dsp = self.pick_time(machine, summer=fm_id in ("cooling_water_low", "fume_extraction_fault"))
        ctx = {"part": r.choice(PARTS_MADE[self.line])}
        if self.line == "welding":
            ctx["fixture_id"] = r.choice(FIXTURES)
            ctx["program_id"] = f"PRG-{r.randint(101, 140)}"
        else:
            ctx["tool_id"] = f"SD-{machine[-2:]}-{r.randint(1, 3)}"
            ctx["screw_lot"] = f"SL-{opened:%y%m}-{r.randint(11, 79)}"
        wrong = r.choice(fm["distinguish_from"]) if ct == "wrong_first" else None
        partner = r.choice(fm["distinguish_from"]) if ct == "composite" else None
        recent, sig = self.signals(fm_id, ct, opened, ctx, mtype)
        if wrong:
            rw, sw = self.signals(wrong, "decoy", opened, ctx, mtype)
            recent += rw
            sig += sw
        if partner:
            rp, sp = self.signals(partner, "typical", opened, ctx, mtype)
            recent += rp
            sig += sp
        recent = list(dict.fromkeys(recent))
        seen, clean = set(), []
        for x in sorted(sig, key=lambda x: x["t"]):
            key = (x["t"][:16], x["signal"], str(x["value"]))
            if key not in seen:
                seen.add(key)
                clean.append(x)
        sig = clean
        by = ["jev", "technician", "rule"][idx % 3]
        if ct == "wrong_first":
            first = {"failure_mode_id": wrong, "by": by, "p": round(r.uniform(0.36, 0.58), 2)}
        elif ct == "composite":
            first = {"failure_mode_id": r.choice([fm_id, partner]), "by": by, "p": round(r.uniform(0.40, 0.62), 2)}
        elif ct == "new":
            first = {"failure_mode_id": fm_id, "by": "jev" if r.random() < 0.6 else by, "p": round(r.uniform(0.28, 0.45), 2)}
        else:
            first = {"failure_mode_id": fm_id, "by": by, "p": round(r.uniform(0.55, 0.91), 2)}
        if first["by"] != "jev":
            first["p"] = None
        new_mode = None
        if ct == "new":
            name, desc = r.choice(NEW_CANDIDATES[FAMILY[fm_id]])
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
            dmin = r.randint(5, 20)
        elif ct in ("escalated", "new"):
            dmin = r.randint(max(120, hi), 600)
        closed = opened + dt.timedelta(minutes=dmin, seconds=r.randint(0, 300))
        self.busy[machine].append((opened, closed))
        src, evt, sev = TRIGGER[fm_id]
        if ct == "false_alarm":
            src = r.choice(["vision", "fused", "mes"])
        if fm_id == "fume_extraction_fault" and any(s["signal"] == "smoke" and s["value"] == "outside_cell" for s in sig) \
                and ct != "false_alarm":
            src, evt, sev = "fused", "smoke_outside_cell", "S3"
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
        entered = (ct != "false_alarm" or r.random() < 0.3) and fm["loto_required"]
        level = r.choices(["L1", "L2", "L3"], [0.3, 0.5, 0.2])[0]
        if fm["category"] == "safety" or ct in ("escalated", "new"):
            level = r.choice(["L2", "L3"])
        esc_to = None
        if ct in ("escalated", "new"):
            pool = {"arc": ["設備工程師", "銲接電源原廠技師"], "robot": ["機器人工程師", "治具廠"], "spot": ["設備工程師", "點銲機原廠技師"],
                    "fume": ["廠務", "廠務"], "screw": ["設備工程師", "品保工程師", "工具原廠技師"], "cable": ["品保工程師", "測試工程師"],
                    "ppe": ["職安人員", "職安人員"]}[FAMILY[fm_id]]
            esc_to = r.choice(pool)
        return {
            "idx": idx, "fm_id": fm_id, "case_type": ct, "machine_id": machine, "machine_type": mtype,
            "opened_at": opened.isoformat(), "closed_at": closed.isoformat(), "shift": shift,
            "trigger": {"source": src, "event_type": evt, "rule_severity": sev},
            "context": {"days_since_pm": dsp, "recent_changes": recent, **ctx,
                        "season": "summer" if opened.month in (6, 7, 8, 9) else "other"},
            "log_signals": sig, "first_hypothesis": first, "final_root_cause": final,
            "contributing_causes": [partner] if partner else [], "hypothesis_correct": first["failure_mode_id"] == final,
            "new_mode": new_mode, "outcome": outcome, "downtime_min": dmin, "sop_used": sop_used,
            "safety_flags": {"loto_done": entered, "danger_zone_entry": entered, "near_miss": False},
            "technician": {"id": r.choice(TECH_IDS[self.line]), "level": level},
            "false_alarm_story": r.choice(FALSE_ALARM_STORY[FAMILY[fm_id]]) if ct == "false_alarm" else None,
            "parts_vocab": PARTS[self.line][fm_id] if ct not in ("false_alarm", "new") else [],
            "escalated_to": esc_to,
        }

    # ---------- signals (shape follows failure_modes/{line}.yaml log_signals)
    def signals(self, fm_id, ct, t0, ctx, mtype):
        r = self.r
        decoy, fa = ct == "decoy", ct == "false_alarm"

        def at(mins, sig, val):
            return {"t": (t0 + dt.timedelta(minutes=mins, seconds=r.randint(0, 50) if mins else 0)).isoformat(),
                    "signal": sig, "value": val}

        def hhmm(mins):
            return (t0 + dt.timedelta(minutes=mins)).strftime("%H:%M")

        u, ri, ch = r.uniform, r.randint, r.choice
        fx = ctx.get("fixture_id", "FX-A")
        s, recent = [], []
        if fm_id == "wire_feed_birdnest":
            if decoy:
                return [], [at(-6, "arc_current_cv_pct", round(u(9, 11.5), 1)), at(-1, "alarm_code", "ARC_LOST")]
            if fa:
                return [], [at(0, "alarm_code", "ARC_LOST"), at(0, "wire_feed_speed_mpm", 6.0), at(1, "robot_state", "running")]
            s += [at(-20, "wire_feed_speed_mpm", 6.0), at(-20, "wire_feed_motor_current_a", round(u(2.2, 2.6), 1)),
                  at(-6, "wire_feed_speed_mpm", round(u(5.1, 5.4), 1)), at(-6, "wire_feed_motor_current_a", round(u(4.6, 5.0), 1)),
                  at(-1, "wire_feed_speed_mpm", round(u(4.3, 4.9), 1)), at(-1, "arc_current_cv_pct", round(u(13, 19), 1)),
                  at(0, "wire_feed_motor_current_a", round(u(5.0, 5.8), 1)), at(0, "alarm_code", ch(["WIRE_FEED_OVERLOAD", "ARC_LOST"])),
                  at(0, "tip_arc_on_min", ri(70, 230))]
        elif fm_id == "contact_tip_wear":
            if decoy:
                return [], [at(-2, "tip_arc_on_min", ri(255, 290)), at(-2, "arc_start_retry_per_100", round(u(4.5, 6.5), 1))]
            if fa:
                return [], [at(0, "alarm_code", "ARC_START_FAIL"), at(0, "arc_start_retry_per_100", 1.2), at(1, "robot_state", "running")]
            s += [at(-60 * 20, "arc_start_retry_per_100", round(u(1.0, 2.0), 1)), at(-60 * 8, "arc_start_retry_per_100", round(u(3.5, 5.5), 1)),
                  at(0, "arc_start_retry_per_100", round(u(9, 15), 1)), at(0, "tip_arc_on_min", ri(320, 430)),
                  at(0, "wire_feed_speed_mpm", round(u(5.9, 6.1), 1)), at(0, "alarm_code", "ARC_START_FAIL")]
            if r.random() < 0.4:
                s.append(at(0, "alarm_code", "WIRE_STICK"))
        elif fm_id == "shielding_gas_low":
            if decoy:
                return [], [at(-8, "weld_defect", f"porosity×{ri(2, 4)} {fx}")]
            if fa:
                return [], [at(0, "alarm_code", "GAS_LOW"), at(0, "gas_flow_lpm", 11.5), at(1, "gas_flow_lpm", 16.5)]
            cyl = r.random() < 0.5
            if cyl:
                m = ri(20, 110)
                recent.append(f"換氣瓶 {hhmm(-m)}")
                s += [at(-m, "consumable_change", "gas_cylinder"), at(-m + 5, "gas_supply_bar", round(u(6, 9), 1))]
            s += [at(-30, "gas_flow_lpm", round(u(16, 18), 1)), at(-5, "gas_flow_lpm", round(u(10.5, 12.5), 1)),
                  at(0, "gas_flow_lpm", round(u(7.5, 11.0), 1)), at(0, "alarm_code", "GAS_LOW"),
                  at(-3, "weld_defect", f"porosity×{ri(4, 9)} 各治具")]
        elif fm_id == "nozzle_spatter_buildup":
            if decoy:
                return [], [at(-1, "torch_clean_interval_min", ri(36, 43))]
            if fa:
                return [], [at(0, "defect_rate_pct", 4.3), at(5, "defect_rate_pct", 1.0), at(0, "torch_clean_interval_min", 18)]
            s += [at(-90, "defect_rate_pct", round(u(0.8, 1.3), 1)), at(-40, "defect_rate_pct", round(u(2.5, 3.6), 1)),
                  at(0, "defect_rate_pct", round(u(4.5, 6.8), 1)), at(0, "torch_clean_interval_min", ri(55, 95)),
                  at(0, "gas_flow_lpm", round(u(15.5, 18.5), 1))]
            if r.random() < 0.6:
                s.append(at(-ri(50, 80), "alarm_code", "CLEAN_STATION_FAULT"))
        elif fm_id == "tcp_drift":
            m = ri(40, 400)
            if decoy:
                recent.append(f"銲槍輕微碰撞 {hhmm(-m - 600)}")
                return recent, [at(-m - 600, "collision_event", "light")]
            if fa:
                return [], [at(0, "alarm_code", "TCP_CHECK_NG"), at(0, "tcp_check_delta_mm", 1.1), at(8, "tcp_check_delta_mm", 0.3)]
            if r.random() < 0.6:
                recent.append(f"撞槍 {hhmm(-m)}")
                s.append(at(-m, "collision_event", ch(["light", "heavy"])))
            else:
                recent.append(f"更換銲槍頸管 {hhmm(-m)}")
                s.append(at(-m, "consumable_change", "torch_neck"))
            s += [at(-m + 30, "weld_defect", f"bead_offset×{ri(3, 8)} 全治具"), at(0, "tcp_check_delta_mm", round(u(1.2, 2.5), 1)),
                  at(0, "arc_voltage_cv_pct", round(u(2.5, 3.8), 1))]
            if r.random() < 0.5:
                s.append(at(0, "alarm_code", "TCP_CHECK_NG"))
        elif fm_id == "ground_poor":
            if decoy:
                return [], [at(-4, "arc_voltage_cv_pct", round(u(8.0, 9.5), 1))]
            if fa:
                return [], [at(0, "arc_voltage_cv_pct", 10.6), at(3, "arc_voltage_cv_pct", 3.2)]
            side = ch(["A 側", "B 側"])
            s += [at(-40, "arc_voltage_cv_pct", round(u(2.5, 3.5), 1)), at(-12, "arc_voltage_cv_pct", round(u(11, 16), 1)),
                  at(0, "arc_voltage_cv_pct", round(u(12, 18), 1)), at(0, "arc_current_a", ri(125, 140)),
                  at(0, "wire_feed_motor_current_a", round(u(2.2, 2.7), 1)), at(-5, "weld_defect", f"spatter×{ri(3, 6)} 工位{side}")]
        elif fm_id == "fixture_fitup_gap":
            m = ri(40, 220)
            lot = f"L-{t0:%y%m}-{ri(10, 99)}"
            recent.append(f"換工件批 {hhmm(-m)}（{lot}）")
            if decoy:
                return recent, [at(-m, "part_lot_change", lot)]
            if fa:
                return recent, [at(-m, "part_lot_change", lot), at(0, "weld_defect", f"burn_through×1 {fx}")]
            dtype = "burn_through" if mtype == "robot_mig_cell" else "spatter"
            s += [at(-m, "part_lot_change", lot), at(-m + 15, "weld_defect", f"{dtype}×{ri(2, 4)} {fx}"),
                  at(0, "weld_defect", f"{ch([dtype, 'lack_of_fusion'])}×{ri(2, 5)} {fx}")]
            s.append(at(0, "arc_voltage_cv_pct", round(u(2.5, 3.8), 1)) if mtype == "robot_mig_cell"
                     else at(0, "weld_current_ka", round(u(9.6, 10.4), 1)))
        elif fm_id == "spot_electrode_wear":
            if decoy:
                return [], [at(-1, "weld_count_since_dress", ri(280, 330))]
            if fa:
                return [], [at(0, "weld_defect", "undersize_nugget×1"), at(0, "weld_count_since_dress", ri(80, 160)), at(0, "tip_dress_count", ri(5, 12))]
            if r.random() < 0.5:
                s.append(at(0, "weld_count_since_dress", ri(360, 480)))
                s.append(at(0, "tip_dress_count", ri(14, 22)))
            else:
                s.append(at(0, "tip_dress_count", ri(26, 32)))
                s.append(at(0, "weld_count_since_dress", ri(150, 260)))
            s += [at(0, "weld_current_ka", round(u(9.6, 10.6), 1)), at(-10, "weld_defect", f"undersize_nugget×{ri(2, 4)}")]
            if r.random() < 0.5:
                s.append(at(-3, "weld_defect", f"spatter×{ri(2, 5)}"))
        elif fm_id == "cooling_water_low":
            spot = mtype == "spot_welder"
            if decoy:
                return [], [at(-5, "coolant_return_temp_c", ri(41, 44))]
            if fa:
                return [], [at(0, "alarm_code", "COOLANT_LOW"), at(0, "coolant_flow_lpm", 2.8 if spot else 0.9),
                            at(1, "coolant_flow_lpm", 4.8 if spot else 2.0)]
            s += [at(-90, "coolant_flow_lpm", round(u(4.5, 5.2), 1) if spot else round(u(1.8, 2.2), 1)),
                  at(-30, "coolant_flow_lpm", round(u(3.0, 3.6), 1) if spot else round(u(1.1, 1.4), 1)),
                  at(0, "coolant_flow_lpm", round(u(1.8, 2.8), 1) if spot else round(u(0.5, 0.9), 1)),
                  at(-30, "coolant_return_temp_c", ri(33, 38)), at(0, "coolant_return_temp_c", ri(47, 56)),
                  at(0, "alarm_code", ch(["COOLANT_LOW", "WELD_OVERTEMP"]))]
        elif fm_id == "fume_extraction_fault":
            if fa:
                return [], [at(0, "smoke", ch(["outside_cell", "in_cell"])), at(0, "exhaust_flow_m3h", ri(1900, 2100)),
                            at(0, "exhaust_fan_state", "running")]
            if r.random() < 0.5:
                s += [at(-25, "exhaust_flow_m3h", ri(1850, 2050)), at(0, "exhaust_fan_state", "stopped"), at(0, "exhaust_flow_m3h", ri(0, 200))]
            else:
                s += [at(-60 * 24, "exhaust_dp_pa", ri(950, 1100)), at(0, "exhaust_dp_pa", ri(1250, 1600)),
                      at(0, "exhaust_flow_m3h", ri(1050, 1350))]
            s.append(at(0, "alarm_code", "FUME_EXH_LOW"))
            if r.random() < 0.6:
                s.append(at(1, "smoke", "outside_cell"))
            s.append(at(2, "robot_state", "cycle_stop"))
        # ---------------- assembly
        elif fm_id == "torque_not_reached":
            if decoy:
                return [], [at(-3, "screw_result", "NOK_LOW_TORQUE")]
            if fa:
                return [], [at(0, "screw_result", "NOK_LOW_TORQUE"), at(1, "screw_result", "OK")]
            holes = r.sample([f"H{i:02d}" for i in range(1, 13)], 3)
            for k, h in enumerate(holes):
                s += [at(-25 + k * 10, "screw_result", "NOK_LOW_TORQUE"), at(-25 + k * 10, "nok_hole_id", h),
                      at(-25 + k * 10, "final_torque_nm", round(u(0.52, 0.68), 2)), at(-25 + k * 10, "final_angle_deg", ri(650, 980))]
            s += [at(0, "rundown_torque_nm", round(u(0.07, 0.12), 2)), at(0, "nok_rate_pct", round(u(2.2, 4.0), 1))]
        elif fm_id == "cross_thread_strip":
            if decoy:
                return [], [at(-6, "screw_result", "NOK_ANGLE_HIGH")]
            if fa:
                return [], [at(0, "screw_result", "NOK_HIGH_TORQUE_EARLY"), at(2, "screw_result", "OK")]
            h = f"H{ri(1, 12):02d}"
            kind = ch(["NOK_HIGH_TORQUE_EARLY", "NOK_ANGLE_HIGH"])
            for k in range(3):
                s += [at(-40 + k * 18, "screw_result", kind), at(-40 + k * 18, "nok_hole_id", h)]
            s.append(at(0, "rundown_torque_nm", round(u(0.36, 0.55), 2)) if kind == "NOK_HIGH_TORQUE_EARLY"
                     else at(0, "final_angle_deg", ri(1950, 2400)))
        elif fm_id == "bit_wear_camout":
            if decoy:
                return [], [at(-1, "bit_cycle_count", ri(33000, 38500))]
            if fa:
                return [], [at(0, "screw_result", "NOK_LOW_TORQUE"), at(0, "bit_cycle_count", ri(8000, 20000))]
            s += [at(-60 * 30, "nok_rate_pct", round(u(0.4, 0.8), 1)), at(-60 * 10, "nok_rate_pct", round(u(1.5, 2.2), 1)),
                  at(0, "nok_rate_pct", round(u(3.2, 5.0), 1)), at(0, "bit_cycle_count", ri(41500, 56000))]
            for k, h in enumerate(r.sample([f"H{i:02d}" for i in range(1, 13)], 2)):
                s += [at(-8 + k * 5, "screw_result", "NOK_LOW_TORQUE"), at(-8 + k * 5, "nok_hole_id", h)]
        elif fm_id == "screw_feeder_jam":
            m = ri(10, 90)
            lot = f"SL-{t0:%y%m}-{ri(11, 79)}"
            recent.append(f"補充螺絲 {hhmm(-m)}（{lot}）")
            if decoy:
                return recent, [at(-m, "screw_lot_change", lot)]
            if fa:
                return [], [at(0, "alarm_code", "FEEDER_TIMEOUT"), at(1, "screw_result", "OK")]
            s += [at(-m, "screw_lot_change", lot), at(-5, "feeder_pick_fail_count", ri(11, 26)),
                  at(0, "alarm_code", "FEEDER_TIMEOUT"), at(0, "screw_result", "NOK_NO_SCREW")]
        elif fm_id == "wrong_screw_type":
            m = ri(15, 150)
            lot = f"SL-{t0:%y%m}-{ri(11, 79)}"
            recent.append(f"換螺絲批 {hhmm(-m)}（{lot}）")
            if decoy:
                return recent, [at(-m, "screw_lot_change", lot)]
            short = r.random() < 0.6
            s += [at(-m, "screw_lot_change", lot), at(-m + 5, "final_angle_deg", ri(600, 820) if short else ri(2000, 2400)),
                  at(0, "screw_result", "NOK_ANGLE_LOW" if short else "NOK_ANGLE_HIGH")]
        elif fm_id == "missing_screw":
            ev = ch(["交接班", "重工", "換線", "補料"])
            m = ri(10, 80)
            recent.append(f"{ev} {hhmm(-m)}")
            if decoy:
                return recent, [at(-2, "screw_missing", True)]
            if fa:
                return [], [at(0, "screw_missing", True), at(0, "screw_count_actual", 12), at(0, "screw_count_required", 12)]
            s += [at(0, "screw_count_actual", ri(10, 11)), at(0, "screw_count_required", 12), at(0, "alarm_code", "COUNT_MISMATCH")]
            if r.random() < 0.7:
                s.append(at(0, "screw_missing", True))
        elif fm_id == "tool_calibration_due":
            if decoy:
                return [], [at(-1, "calib_due_days", ri(2, 6))]
            if fa:
                return [], [at(0, "torque_audit_dev_pct", 11.2), at(10, "torque_audit_dev_pct", 2.1)]
            if r.random() < 0.6:
                s += [at(-60 * 24, "calib_due_days", -ri(3, 25)), at(-60 * 24, "alarm_code", "CALIB_OVERDUE")]
            s += [at(0, "torque_audit_dev_pct", ch([1, -1]) * round(u(11, 17), 1)), at(-10, "screw_result", "OK")]
        elif fm_id == "cable_pinch_misroute":
            if decoy:
                return [], [at(-15, "test_result", "LINK_INTERMITTENT")]
            if fa:
                return [], [at(0, "cable_out_of_path", True), at(2, "test_result", "PASS")]
            if r.random() < 0.6:
                s.append(at(-20, "cable_out_of_path", True))
            if r.random() < 0.5:
                s += [at(0, "test_result", "HIPOT_FAIL"), at(0, "insulation_mohm", round(u(1.5, 8.0), 1))]
            else:
                s += [at(-6, "test_result", "LINK_INTERMITTENT"), at(0, "test_result", "LINK_INTERMITTENT")]
        elif fm_id == "connector_not_seated":
            if decoy:
                return [], [at(-10, "test_result", "DEVICE_NOT_FOUND")]
            if fa:
                return [], [at(0, "test_result", "DEVICE_NOT_FOUND"), at(4, "test_result", "PASS")]
            s += [at(-4, "test_result", "DEVICE_NOT_FOUND"), at(0, "test_result", "DEVICE_NOT_FOUND"),
                  at(0, "insulation_mohm", ri(250, 900))]
            if r.random() < 0.5:
                s.append(at(-1, "connector_unlatched", True))
        elif fm_id == "ppe_esd_missing":
            if fa:
                return [], [at(0, "ppe_missing", ch(["cut_gloves", "safety_glasses"])), at(0, "ppe_stock_count", ri(15, 30))]
            if r.random() < 0.5:
                s.append(at(0, "ppe_missing", ch(["cut_gloves", "safety_glasses", "esd_strap"])))
            else:
                s += [at(0, "esd_check", "FAIL"), at(0, "alarm_code", "ESD_CHECK_FAIL")]
            s.append(at(0, "ppe_stock_count", ri(0, 2) if r.random() < 0.4 else ri(12, 30)))
        if fa and not s:
            s = [at(0, "alarm_code", "SCREW_NOK" if self.line == "assembly" else "ARC_LOST")]
        return recent, sorted(s, key=lambda x: x["t"])
