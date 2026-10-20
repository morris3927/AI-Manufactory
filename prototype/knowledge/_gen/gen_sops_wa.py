"""Generate welding / assembly SOP drafts (knowledge/sop/{welding,assembly}/SOP-*.yaml).

Same pipeline as gen_sops.py (stamping, untouched):
  1. draft    — DeepSeek V4.1 Flash writes ONLY inspect / repair / verify (+ title, symptoms,
                prerequisites, escalate_if) for non-safety modes → _gen/raw/sop/{line}/
  2. assemble — program adds the fixed prepare / isolate / restore scaffolding (LOTO reference,
                設施規則第57/254條、工業用機器人危害預防標準、動火許可 where relevant), inserts the
                testing/positioning pre-step before any energized step, applies the reviewed fixes in
                sop_patches_weld_asm.yaml and writes YAML. Safety-category modes never go to the LLM.

Usage:
  python3.13 gen_sops_wa.py draft --line welding [--only fm_id] [--force]
  python3.13 gen_sops_wa.py assemble --line welding
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys

import yaml

from common import GEN, KB, SAFETY_BLACKLIST, catalog, guidance_chunks, load_yaml, loto_files, regulation_chunks, sources
from gen_sops import SCHEMA, Dumper, deep, fullwidth, step
from lines import LINES

PROMPT_VER = "sop-gen-wa-v1"
PATCHES = GEN / "sop_patches_weld_asm.yaml"
EXTRA_BLACKLIST = ["釋放煞車", "放寬判定", "關閉計數", "修改安全參數"]

# welding: modes whose repair may need grinding / re-welding outside the cell → 動火許可
HOT_WORK = {"fixture_fitup_gap", "ground_poor"}
# welding: modes whose repair needs robot jogging / re-teaching → teach-mode step (robot standard 第26、27條)
ROBOT_TEACH = {"tcp_drift", "fixture_fitup_gap", "wire_feed_birdnest", "contact_tip_wear", "nozzle_spatter_buildup"}
# assembly: modes worked at the manual / test station (DUT power instead of screwdriver)
DUT_MODES = {"cable_pinch_misroute", "connector_not_seated"}

CHUNK_HINT = {  # chunk ids offered to the LLM as citable refs, per line
    "welding": ["REG-N0060009-292", "REG-N0060009-284", "REG-N0060009-106", "REG-N0060009-173", "REG-N0060009-245",
                "REG-N0060025-27", "REG-N0060025-28", "REG-N0060025-29", "REG-N0060025-26", "REG-N0060027-60",
                "REG-OSHA-1910.252-c-3", "REG-OSHA-1910.254-c-2", "REG-OSHA-1910.254-d-2", "REG-OSHA-1910.254-d-3",
                "REG-OSHA-1910.254-d-4", "REG-OSHA-1910.254-d-9", "REG-OSHA-1910.255-b-8", "REG-OSHA-1910.255-e",
                "PUB-OSHA-FS-3647-reducing-exposure"],
    "assembly": ["REG-OSHA-1910.147-a-2-iii-A", "REG-N0060027-77", "REG-N0060009-324-1",
                 "PUB-NASA-STD-8739.4A-C4-table-7-1", "PUB-NASA-STD-8739.4A-C4-7.2.28-support",
                 "PUB-NASA-STD-8739.4A-C4-16.1-handling", "PUB-NASA-STD-8739.4A-C4-17.2.1-mating",
                 "PUB-NASA-STD-8739.4A-C4-20.5.1-ties-assemblies"],
}
LINE_SOURCES = {"welding": {"L1", "L7", "L20", "L21", "W1", "W2", "W4", "W5", "W6", "W8", "W9", "W10"},
                "assembly": {"L1", "L7", "A1", "A2", "A3", "A4", "A5"}}
TITLE_EXAMPLE = {"welding": "導電嘴磨耗／回燒之檢查與更換", "assembly": "起子頭磨耗（打滑）之檢查與更換"}


def raw_dir(line):
    return GEN / "raw" / "sop" / line


def out_dir(line):
    return KB / "sop" / line


def loto_ids(line, fm) -> list[str]:
    ids = []
    for t in fm["machine_types"]:
        lid = LINES[line]["loto_by_type"][t]
        if lid not in ids:
            ids.append(lid)
    return ids


def loto_phrase(line, fm) -> str:
    ids = loto_ids(line, fm)
    if line == "assembly":
        if fm["machine_types"] == ["manual_assembly_station"]:
            return ("依 LOTO-ASM-TOOL-01 執行輕量上鎖：關閉測試站輸出並拔除受測機殼電源線、套上插頭鎖蓋與個人標籤，"
                    "等待電源供應器放電時間到後才接觸機殼內線材與連接器；插頭保持在自己的專屬控制下")
        return ("依 LOTO-ASM-TOOL-01 執行輕量上鎖：拔下起子控制器插頭並套上插頭鎖蓋（充電式起子取下電池）、"
                "關閉送釘機氣源並洩壓，掛上個人標籤；插頭保持在自己的專屬控制下，並按啟動鍵確認不動作")
    names = LINES[line]["loto_name"]
    parts = " 或 ".join(f"{i}（{names[i]}）" for i in ids)
    return f"依 {parts}執行停機、隔離、上鎖掛牌、洩除殘能並驗證零能量"


def build_prompt(line, fm_id, fm, cat, src, lotos, chunks):
    cfg = LINES[line]
    system = (GEN / "prompts" / "sop_gen_wa_v1.system.md").read_text(encoding="utf-8").format(
        context=cfg["context"], terms=cfg["terms"])
    tmpl = (GEN / "prompts" / "sop_gen_wa_v1.user.md").read_text(encoding="utf-8")
    fm_yaml = yaml.safe_dump({fm_id: fm}, allow_unicode=True, sort_keys=False, width=200)
    machines = "\n".join(f"- {t}：{cfg['machine_type_label'][t]}" for t in fm["machine_types"])
    loto_summary = []
    for lid in loto_ids(line, fm):
        l = lotos[lid]
        es = "；".join(f"{e['id']} {e['type']}：{e['device']}" for e in l["energy_sources"])
        loto_summary.append(f"- {lid}（{l['title']}）能源：{es}")
    distinguish = "\n".join(f"- {d}：{cat[d]['label']} —— criteria：{cat[d]['criteria']}（{cat[d]['sop']}）"
                            for d in fm["distinguish_from"]) or "（無）"
    targets = sorted({cat[d]["sop"] for d in fm["distinguish_from"]} |
                     {v["sop"] for k, v in cat.items() if k != fm_id and set(v["machine_types"]) & set(fm["machine_types"])})
    labels = {v["sop"]: v["label"] for v in cat.values()}
    escalate_targets = "\n".join(f"- {s}：{labels[s]}" for s in targets)
    allowed = set(fm["sources"]) | LINE_SOURCES[line]
    srcs = "\n".join(f"- {k}：{v['title']}（授權 {v['license']}，用法 {v['use']}）" for k, v in src.items() if k in allowed)
    ch = "\n".join(f"- {c}：{chunks[c]['title']}" for c in CHUNK_HINT[line] if c in chunks)
    user = tmpl.format(line=line, fm_id=fm_id, fm_yaml=fm_yaml, machines=machines, loto_summary="\n".join(loto_summary),
                       distinguish=distinguish, escalate_targets=escalate_targets, sources=srcs, chunks=ch,
                       title_example=TITLE_EXAMPLE[line], skill_levels=cfg["skill_levels"], ppe_base="、".join(cfg["ppe_base"]))
    return system, user


def draft(line, only, force):
    from llm import MODEL, chat_json

    cat, src, lotos = catalog(line), sources(), loto_files()
    chunks = {**regulation_chunks(), **guidance_chunks()}
    raw = raw_dir(line)
    raw.mkdir(parents=True, exist_ok=True)
    jobs = [(k, v) for k, v in cat.items() if not v["safety_only_sop"] and (only is None or k == only)]

    def run(item):
        fm_id, fm = item
        p = raw / f"{fm['sop']}.json"
        if p.exists() and not force:
            return fm_id, "cached"
        system, user = build_prompt(line, fm_id, fm, cat, src, lotos, chunks)
        seed = int(re.sub(r"\D", "", fm["sop"])) + (100 if line == "welding" else 200)
        try:
            out = chat_json(system, user, SCHEMA, temperature=0.4, seed=seed, retries=5)
        except Exception as e:  # noqa: BLE001
            return fm_id, f"FAILED {e}"
        p.write_text(json.dumps({"generator": {"model": MODEL, "prompt_ver": PROMPT_VER, "seed": seed},
                                 "failure_mode_id": fm_id, "output": out}, ensure_ascii=False, indent=2), encoding="utf-8")
        return fm_id, "ok"

    with cf.ThreadPoolExecutor(6) as ex:
        for fm_id, status in ex.map(run, jobs):
            print(fm_id, status, flush=True)


# ---------------------------------------------------------------- patches
def apply_patches(sop_id: str, body: dict) -> dict:
    pt = load_yaml(PATCHES) if PATCHES.exists() else {}
    s = json.dumps(body, ensure_ascii=False)
    for a, b in pt.get("global_replace", []) or []:
        s = s.replace(a, b)
    per = (pt.get("per_sop") or {}).get(sop_id, {})
    for a, b in per.get("replace", []) or []:
        if a not in s:
            print(f"  WARN {sop_id}: patch not matched: {a[:30]}")
        s = s.replace(a, b)
    body = json.loads(s)
    for item in per.get("set", []) or []:
        ph, idx, field = item["path"].split(".")
        body[ph][int(idx)][field] = item["value"]
    for item in per.get("drop", []) or []:  # e.g. "repair.2"
        ph, idx = item.split(".")
        body[ph][int(idx)] = None
    for item in per.get("insert", []) or []:  # {phase, at, step}
        body[item["phase"]].insert(int(item["at"]), item["step"])
    for ph in ("inspect", "repair", "verify"):
        body[ph] = [x for x in body[ph] if x is not None]
    for k in ("escalate_if", "symptoms"):
        if k in per:
            body[k] = per[k]
    return body


# ---------------------------------------------------------------- safety-only templates (no LLM)
def safety_only(line, fm_id, fm):
    if fm_id == "fume_extraction_fault":
        lid = "LOTO-WELD-ROBOT-01"
        title = "排煙設備（局部排氣）異常之停止銲接與通報"
        steps = [
            ("prepare", "立即停止銲接：機器人以循環停止停下、點銲機停止，不得在局部排氣異常時繼續銲接或試銲（設施規則第292條）",
             "銲接進行中工位 ROI 內的火花與銲煙屬正常；本 SOP 處理的是排氣失效、煙霧滯留或擴散到工位外", False,
             ["REG-N0060009-292", "REG-OSHA-1910.252-c-1-iii"]),
            ("prepare", "煙霧已擴散到工位外時，請附近人員離開煙霧區到上風處；有人出現眼鼻喉刺激、頭暈或噁心，立即離開並就醫；通知現場主管與廠務",
             "以人員安全為優先，先退避再處理設備", True, ["PUB-OSHA-FS-3647-health-effects", "REG-N0060001-18"]),
            ("isolate", "由現場主管指派人員依 LOTO-WELD-ROBOT-01（機器人銲接工作站）或 LOTO-SPOT-WELD-01（點銲機）將受影響的銲接設備停機上鎖，並在操作盤掛「排氣異常・禁止銲接」標籤",
             "排氣恢復並經確認前，銲接設備不得復電試銲", True, ["REG-N0060009-57-p1", "REG-OSHA-1910.147-d-4"]),
            ("inspect", "交由廠務或合格人員檢查局部排氣裝置（排風機、皮帶、濾筒、導管與氣罩），依職業安全衛生管理辦法第40、47條辦理；一般技術員與 AI 只記錄風量、濾筒壓差、排風機狀態與時間，AI 不提供排除方法",
             "更換或清理濾筒時，濾筒內的粉塵可能含銲接燻煙金屬成分，依廠務規定處理", True, ["REG-N0060027-40", "REG-N0060027-47"]),
            ("verify", "合格人員修復後實施重點檢查（吸氣及排氣能力）並留下紀錄；現場主管確認風量回到設定範圍、煙霧不再擴散後，才在 Kanban 簽核",
             None, True, ["REG-N0060027-47", "REG-OSHA-1910.252-c-3"]),
            ("restore", f"依 {lid}（或 LOTO-SPOT-WELD-01）解除程序，由原上鎖人親自取下自己的鎖具與標籤（設施規則第254條）；AI 不得宣告可以復電", None, True,
             ["REG-N0060009-254-p2", "REG-OSHA-1910.147-e-3"]),
        ]
        escalate = [
            "銲接機器人或點銲機已停止，煙霧仍持續產生、或工位外出現火光 → 疑似火災，依廠內緊急應變程序立即通報主管與廠務；火警自動警報設備為法定設備，AI 不得使其靜音，也不得把本事件當成一般維修",
            "有人出現吸入燻煙症狀或受傷 → 急救優先、保全現場，協助主管依規定通報（職安法第37條）",
            "同一排氣系統一週內重複異常 → 通知廠務安排局部排氣裝置定期檢查（管理辦法第40條）",
        ]
        symptoms = ["排煙機停了", "煙散不掉", "煙霧飄出工位", "濾筒壓差警報", "排氣風量低"]
        prereq = {"tools": ["Kanban 工單", "「排氣異常・禁止銲接」標籤", "個人鎖與標籤"], "parts": [], "skill_level": "現場主管／廠務合格人員"}
        legal = ["設施規則第292條", "職業安全衛生管理辦法第40、47條", "設施規則第57條", "設施規則第254條", "職業安全衛生法第18條"]
        ids = ["LOTO-WELD-ROBOT-01", "LOTO-SPOT-WELD-01"]
        lessons = "排氣異常只做停止銲接、人員退避、通知與上鎖，修復交給廠務；機器人停了還在冒煙就當火警處理。"
    else:  # ppe_esd_missing
        lid, ids = None, []
        title = "防護具（PPE）與靜電防護未落實之提醒與處理"
        steps = [
            ("prepare", "影像或靜電測試站告警後，由班長到站確認；該作業員先暫停接觸工件（靜電測試未通過時不得觸碰主機板等靜電敏感零件），設備不需停機上鎖",
             "提醒為主、不懲罰；畫面中人臉已模糊", False, ["REG-N0060009-277"]),
            ("prepare", "先確認系統面：該站防護具（防割手套、護目鏡、靜電手環）庫存、尺寸是否合適、是否損壞（設施規則第277條）；庫存不足時由班長向倉管申請補發",
             "系統問題優先，不以個人為對象究罰", True, ["REG-N0060009-277"]),
            ("inspect", "交由班長確認未落實的項目與原因（沒有發放、尺寸不合、破損、太熱、忘記），記錄在 Kanban；AI 只記錄時間、站別與事件類型，不做個人懲處判斷，也不提供任何排除方法",
             None, True, ["REG-N0060009-277"]),
            ("verify", "班長補發或更換防護具後，作業員重新佩戴；靜電手環重新測試通過，班長在 Kanban 簽核", None, True, ["REG-N0060009-277"]),
            ("restore", "班長確認後才恢復該站作業；AI 不得宣告可以恢復作業。本事件紀錄只用於改善防護具供應與作業設計，不得用於個人考核", None, True, ["none"]),
        ]
        escalate = [
            "同一站一週內重複發生 → 班長與職安人員檢討防護具選型、尺寸與供應（設施規則第277條）",
            "靜電手環測試站本身異常（多人同時不通過） → 通知品保工程師檢查測試站",
            "有人被鈑金銳邊割傷 → 急救優先，通知主管並記錄",
        ]
        symptoms = ["沒戴手套", "沒戴護目鏡", "沒戴靜電手環", "靜電測試沒過", "手套破了沒換"]
        prereq = {"tools": ["Kanban 工單", "備用防護具"], "parts": [], "skill_level": "班長"}
        legal = ["設施規則第277條", "職業安全衛生法第18條"]
        lessons = "PPE 告警先問「有沒有發、合不合用」，再提醒個人；不懲罰、不做考核。"
    out = [step(i, ph, a, caution=c, sup=s, ref=r) for i, (ph, a, c, s, r) in enumerate(steps, 1)]
    return {"title": title, "symptoms": symptoms, "steps": out, "escalate_if": escalate, "prerequisites": prereq,
            "lessons": lessons, "legal": legal, "loto_id": lid, "loto_ids": ids,
            "generator": {"model": None, "prompt_ver": "safety-template-wa-v1", "seed": None}}


# ---------------------------------------------------------------- assemble
def assemble_mode(line, fm_id, fm, cat, src, lotos):
    cfg = LINES[line]
    types = fm["machine_types"]
    if fm["safety_only_sop"]:
        b = safety_only(line, fm_id, fm)
        steps, gen, lid, ids, legal = b["steps"], b["generator"], b["loto_id"], b["loto_ids"], b["legal"]
        body = b
    else:
        raw = json.loads((raw_dir(line) / f"{fm['sop']}.json").read_text(encoding="utf-8"))
        body, gen = apply_patches(fm["sop"], raw["output"]), raw["generator"]
        ids = loto_ids(line, fm)
        lid = ids[0]
        steps = []

        def add(phase, action, **kw):
            steps.append(step(len(steps) + 1, phase, action, **kw))

        legal = ["設施規則第57條", "設施規則第254條", "OSHA 1910.147(d)"]
        if line == "welding":
            add("prepare", "通知操作員與班長停線，於 Kanban 開立維修單；記錄停機當下的警報碼、近 15 分鐘訊號趨勢與不良銲道位置（治具、工位）",
                ref=["REG-OSHA-1910.147-d-1"])
            hot = fm_id in HOT_WORK
            add("prepare", "確認危害防止計畫與作業許可單已由現場主管核准（設施規則第57條）" +
                ("；若修復需要在銲接工作站以外研磨、補銲或切割，另依廠內規定申請動火許可，作業前清除周圍可燃物並備妥滅火器（設施規則第173條）" if hot else ""),
                sup=True, ref=["REG-N0060009-57-p1"] + (["REG-N0060009-173", "REG-OSHA-1910.252-a-2-iv"] if hot else []))
            add("isolate", loto_phrase(line, fm), caution=body["isolate_caution"], sup=True,
                ref=["REG-N0060009-57-p1", "REG-N0060009-57-v20240801", "REG-OSHA-1910.147-d-6"])
            if "robot_mig_cell" in types:
                add("isolate", ("（機器人工作站）" if len(types) > 1 else "") +
                    "進入機器人圍欄前，在啟動開關處掛「禁止觸動」標示並攜帶自己的安全栓（模式鑰匙），圍欄外安排監視人員；銲槍、導電嘴與工件可能仍高溫，等待冷卻或戴耐熱手套（工業用機器人危害預防標準第26、33條）",
                    caution="機器人停在維修姿勢，不得手動鬆開各軸煞車", ref=["REG-N0060025-33", "REG-N0060025-26"])
                legal.insert(2, "工業用機器人危害預防標準第26、33條")
            if "spot_welder" in types:
                add("isolate", ("（點銲機）" if len(types) > 1 else "") +
                    "手要進入上下電極之間前，確認加壓氣缸已洩壓，並插入安全插銷或在電極之間放入墊塊，確認上電極不會下降；電極帽可能仍高溫",
                    caution="未插安全插銷或放墊塊前，手不得放在電極之間", ref=["REG-OSHA-1910.255-b-8"])
                legal.append("OSHA 1910.255(b)(8)")
            if hot:
                legal.insert(2, "設施規則第173條（動火前清除可燃物）")
            legal.append("設施規則第292條（試銲時局部排氣須運轉）")
        else:
            dut = fm_id in DUT_MODES
            add("prepare", "通知班長本站暫停，於 Kanban 開立維修單；記錄" +
                ("測試站結果、受影響機殼序號與畫面" if dut else "NOK 結果、孔位與近 30 件的鎖附扭力／角度紀錄"),
                ref=["REG-OSHA-1910.147-d-1"])
            add("prepare", "可疑工件先隔離：本站與下游已完成、可能受影響的機殼貼「待確認」標籤並移到隔離區，依序號追溯，判定前不得出貨",
                ref=["none"])
            add("prepare", "確認班長已核准簡化作業許可（設施規則第57條）", sup=True, ref=["REG-N0060009-57-p1"])
            add("isolate", loto_phrase(line, fm), caution=body["isolate_caution"], sup=False,
                ref=["REG-N0060009-57-p1", "REG-OSHA-1910.147-a-2-iii-A"])
            legal.insert(2, "OSHA 1910.147(a)(2)(iii)(A)（插頭式設備）")
        tp = "；".join(lotos[lid]["testing_positioning"])
        announced = False
        teach_added = False
        for phase in ("inspect", "repair", "verify"):
            for s in body[phase]:
                if s.get("requires_energized") and not announced:
                    if line == "welding":
                        caution = "通電期間手不得進入機器人可動範圍或電極之間；試銲前確認局部排氣運轉、人員退到圍欄或遮光簾外（設施規則第292條）"
                        ref = ["REG-OSHA-1910.147-f-1", "REG-N0060025-26", "REG-N0060009-292"]
                    else:
                        caution = "通電期間手指不得放在起子頭、送釘吸嘴下方或受測機殼內"
                        ref = ["REG-OSHA-1910.147-f-1", "REG-OSHA-1910.147-a-2-iii-A"]
                    add(phase, f"需要通電量測、移動設備、試銲或試鎖時，依 {lid} 測試／定位程序：{tp}", caution=caution, sup=True, ref=ref)
                    announced = True
                if (line == "welding" and phase == "repair" and fm_id in ROBOT_TEACH and "robot_mig_cell" in types
                        and not teach_added):
                    if not announced:
                        add(phase, f"需要通電量測、移動設備、試銲或試鎖時，依 {lid} 測試／定位程序：{tp}",
                            caution="通電期間手不得進入機器人可動範圍或電極之間；試銲前確認局部排氣運轉、人員退到圍欄或遮光簾外（設施規則第292條）",
                            sup=True, ref=["REG-OSHA-1910.147-f-1", "REG-N0060025-26", "REG-N0060009-292"])
                        announced = True
                    add("repair", "需要移動機器人（修正教導點、校正 TCP、移到換件姿勢）時，一律在教導模式降速操作，持教導器者握住致能開關、放手即停，圍欄外有監視人員；不得修改機器人的安全相關參數（工業用機器人危害預防標準第26、27條）",
                        sup=True, energized=True, ref=["REG-N0060025-27", "REG-N0060025-26"])
                    teach_added = True
                add(phase, s["action"], criteria=s.get("criteria"), caution=s.get("caution"),
                    sup=bool(s.get("requires_supervisor", False)), energized=bool(s.get("requires_energized")),
                    ref=s.get("ref") or ["none"])
        if teach_added:
            legal.insert(3, "工業用機器人危害預防標準第27條（教導作業）")
        if line == "welding":
            add("restore", f"依 {lid} 解除程序：清點工具、恢復安全門連鎖與護罩、確認人員離開機器人可動範圍與電極區後，由原上鎖人親自取下自己的鎖具與標籤（設施規則第254條）；AI 不得宣告可以復電",
                sup=True, ref=["REG-N0060009-254-p2", "REG-OSHA-1910.147-e-3", "REG-N0060025-32"])
        else:
            add("restore", f"依 {lid} 解除程序：清點工具與螺絲、恢復護蓋後，由原上鎖人親自取下自己的插頭鎖蓋與標籤再插回電源（設施規則第254條）；AI 不得宣告可以復電",
                sup=False, ref=["REG-N0060009-254-p2", "REG-OSHA-1910.147-e-3"])
            add("restore", "隔離中的可疑工件由品保判定重工或放行，首件由班長確認後才恢復量產", sup=True, ref=["none"])
        add("restore", "在 Kanban 填寫實際原因、處置內容與處置後是否恢復正常（J5 結案回寫）", ref=["none"])
    verify = [s["action"].rstrip("。") + (f"；判定：{s['criteria']}" if s.get("criteria") else "") for s in steps if s["phase"] == "verify"]
    restore = [s["action"] for s in steps if s["phase"] == "restore"]
    ppe = list(cfg["ppe_base"])
    if line == "welding" and not fm["safety_only_sop"]:
        ppe.append("銲接面罩或遮光面罩（試銲觀察時，設施規則第284條）")
    for p in body.get("ppe", []) or []:
        if p and not any(p in x or x in p for x in ppe):
            ppe.append(p)
    energy = []
    for l in ids:
        for e in lotos[l]["energy_sources"]:
            if e["type"] not in energy:
                energy.append(e["type"])
    used = {r for s in steps for r in s["ref"]}
    refs = []
    for code in fm["sources"] + sorted(x for x in used if x in src and x not in fm["sources"]):
        v = src[code]
        refs.append({"id": code, "title": v["title"], "url": v["url"], "license": v["license"],
                     "use": "paraphrase" if v["license"].startswith("©") else ("quote" if v["use"] == "ingest" else "reference")})
    regs = sorted(x for x in used if x.startswith("REG-"))
    pubs = sorted(x for x in used if x.startswith("PUB-"))
    if regs:
        refs.append({"id": "public/regulations*.jsonl", "title": "法規條文 chunk：" + "、".join(regs),
                     "url": "https://law.moj.gov.tw/ ; https://www.ecfr.gov/", "license": "TW-law / PD", "use": "quote"})
    if pubs:
        refs.append({"id": "public/guidance_weld_asm.jsonl", "title": "公開文件 chunk：" + "、".join(pubs),
                     "url": "https://www.osha.gov/ ; https://standards.nasa.gov/ ; https://www.hse.gov.uk/", "license": "PD / OGL", "use": "quote"})
    doc = {
        "sop_id": fm["sop"], "version": "0.1", "title": body["title"], "line": line,
        "machine_type": types, "failure_mode_ids": [fm_id],
        "data_label": "模擬資料", "synthetic": True, "author": "team_sentinel", "reviewed_by": [],
        "review_status": "draft", "category": fm["category"], "safety_only": fm["safety_only_sop"],
        "generator": gen,
        "safety": {"loto_required": fm["loto_required"], "loto_checklist_id": lid, "loto_checklist_ids": ids,
                   "energy_sources": energy, "ppe": ppe, "danger_zone_entry": fm["loto_required"], "legal_basis": legal},
        "symptoms": list(dict.fromkeys(fm["symptoms"] + body["symptoms"])),
        "prerequisites": body["prerequisites"],
        "steps": steps, "verify": verify, "restore": restore,
        "escalate_if": body["escalate_if"], "lessons": body.get("lessons"), "references": refs,
    }
    return doc


def assemble(line):
    cat, src, lotos = catalog(line), sources(), loto_files()
    out = out_dir(line)
    out.mkdir(parents=True, exist_ok=True)
    review = load_yaml(PATCHES).get("review", {}) if PATCHES.exists() else {}
    for fm_id, fm in cat.items():
        doc = deep(assemble_mode(line, fm_id, fm, cat, src, lotos), fullwidth)
        if review.get("status") == "approved":
            for st in doc["steps"]:  # reviewer confirmed every [ref:none] step (quarantine / record / first-article practice)
                if st["phase"] in ("inspect", "repair", "verify") and st["ref"] == ["none"] and "現場經驗（模擬）" not in st["action"]:
                    st["action"] += "（現場經驗（模擬））"
            doc["review_status"] = "approved"
            doc["reviewed_by"] = review["reviewed_by"]
            doc["review_note"] = review["note"]
        text = yaml.dump(doc, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=1000)
        rs = doc["review_status"]
        header = (f"# {doc['sop_id']} {doc['title']}\n# data_label: 模擬資料 —— 團隊依公開法規與公開技術文件撰寫之模擬 SOP，"
                  f"與任何企業實際製程無關；數值門檻皆為模擬值。review_status: {rs}" +
                  ("（AI 角色扮演審核，非真人專家審核）" if rs == "approved" else "（待審核）") + "\n")
        (out / f"{doc['sop_id']}.yaml").write_text(header + text, encoding="utf-8")
        txt = json.dumps(doc["steps"], ensure_ascii=False)
        bad = [w for w in SAFETY_BLACKLIST + EXTRA_BLACKLIST if w in txt]
        print(doc["sop_id"], fm_id, len(doc["steps"]), "steps", ("BLACKLIST:" + ",".join(bad)) if bad else "")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["draft", "assemble"])
    ap.add_argument("--line", required=True, choices=sorted(LINES))
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(GEN))
    draft(a.line, a.only, a.force) if a.cmd == "draft" else assemble(a.line)
