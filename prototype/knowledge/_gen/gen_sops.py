"""Generate stamping SOP drafts (knowledge/sop/stamping/SOP-*.yaml).

Pipeline (07_RAG知識庫資料.md §7.1 step 2–3):
  1. draft    — for each non-safety failure mode, DeepSeek V4.1 Flash writes ONLY the
                inspect / repair / verify content (+ title, symptoms, prerequisites, escalate_if)
                → raw JSON saved under _gen/raw/sop/ (reproducible input to step 2)
  2. assemble — program wraps the LLM content with fixed prepare / isolate / restore steps
                (LOTO reference, 第57/69/70條、第254條 for electrical re-energizing, OSHA 1910.147(e)(3) for lock removal), adds testing-positioning pre-steps before
                any energized step, and writes YAML. Safety-category modes (safety_only_sop)
                never go to the LLM: their SOP is a fixed stop / notify / wait-for-supervisor text.

Usage:
  python3.13 gen_sops.py draft [--only fm_id] [--force]
  python3.13 gen_sops.py assemble
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys

import yaml

from common import (GEN, KB, LINE, MACHINE_TYPE_LABEL, SAFETY_BLACKLIST, catalog, load_yaml, loto_files,
                    loto_ids_for, sources)

PROMPT_VER = "sop-gen-v1"
RAW = GEN / "raw" / "sop"
OUT = KB / "sop" / LINE

# modes whose repair work puts hands between upper and lower die → safety block (第69條)
DIE_SPACE = {"material_thickness_dev", "die_misalignment", "slug_pulling", "punch_edge_wear",
             "die_lubrication_insufficient", "misfeed_sensor_fault", "stripper_spring_fatigue",
             "overload_tonnage", "counterbalance_misset", "scrap_chute_jam", "shut_height_misset",
             "coil_camber"}
# modes that involve adjusting the die / slide → inching (第70條)
DIE_ADJUST = {"die_misalignment", "shut_height_misset", "counterbalance_misset", "punch_edge_wear",
              "stripper_spring_fatigue", "slug_pulling", "overload_tonnage"}
PRIMARY_LOTO = {  # demo line L1 is servo press + servo feeder
    "servo_roll_feeder": "LOTO-FEED-01", "servo_press": "LOTO-PRESS-SERVO-01",
}

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"}, "criteria": {"type": ["string", "null"]},
        "caution": {"type": ["string", "null"]}, "requires_energized": {"type": "boolean"},
        "requires_supervisor": {"type": "boolean"},
        "ref": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "requires_energized", "ref"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "symptoms": {"type": "array", "items": {"type": "string"}},
        "prerequisites": {"type": "object", "properties": {
            "tools": {"type": "array", "items": {"type": "string"}},
            "parts": {"type": "array", "items": {"type": "string"}},
            "skill_level": {"type": "string"}}, "required": ["tools", "parts", "skill_level"]},
        "ppe": {"type": "array", "items": {"type": "string"}},
        "isolate_caution": {"type": "string"},
        "inspect": {"type": "array", "items": STEP_SCHEMA},
        "repair": {"type": "array", "items": STEP_SCHEMA},
        "verify": {"type": "array", "items": STEP_SCHEMA},
        "escalate_if": {"type": "array", "items": {"type": "string"}},
        "lessons": {"type": "string"},
    },
    "required": ["title", "symptoms", "prerequisites", "ppe", "isolate_caution", "inspect", "repair",
                 "verify", "escalate_if", "lessons"],
}


def primary_loto(fm: dict) -> str:
    for t in fm["machine_types"]:
        if t in PRIMARY_LOTO:
            return PRIMARY_LOTO[t]
    return loto_ids_for(fm["machine_types"])[0]


def all_loto(fm: dict) -> list[str]:
    ids = loto_ids_for(fm["machine_types"])
    if "servo_roll_feeder" in fm["machine_types"]:
        # feeder work near the die entry needs the downstream press locked too (LOTO-FEED-01 F6)
        for x in ("LOTO-PRESS-SERVO-01", "LOTO-PRESS-MECH-01"):
            if x not in ids:
                ids.append(x)
    ids.sort(key=lambda x: x != primary_loto(fm))
    return ids


def loto_phrase(fm: dict) -> str:
    ids = loto_ids_for(fm["machine_types"])
    names = {"LOTO-FEED-01": "送料機", "LOTO-PRESS-SERVO-01": "伺服沖床", "LOTO-PRESS-MECH-01": "機械沖床"}
    if ids == ["LOTO-FEED-01"]:
        return ("依 LOTO-FEED-01 執行停機、隔離、上鎖掛牌、洩除殘能並驗證零能量；手需接近模具入口時，"
                "下游沖床同時依 LOTO-PRESS-SERVO-01（伺服沖床）或 LOTO-PRESS-MECH-01（機械沖床）上鎖")
    # review 2026-10-21（press chief）：原寫法「A 或 B 或 C」會被讀成三選一，送料機與沖床必須「同時」上鎖
    presses = [i for i in ids if i != "LOTO-FEED-01"]
    parts = " 或 ".join(f"{i}（{names[i]}）" for i in presses)
    if "LOTO-FEED-01" in ids:
        return (f"沖床依機台類型 {parts}，送料機同時依 LOTO-FEED-01（送料機）執行停機、隔離、上鎖掛牌、洩除殘能並驗證零能量"
                "（第57條「停止送料」；兩台都要上鎖，不是擇一）")
    return (f"依 {parts}執行停機、隔離、上鎖掛牌、洩除殘能並驗證零能量；"
            "送料機依各檢查表的送料線隔離點連動上鎖（兩台都要上鎖，不是擇一）")


def build_prompt(fm_id: str, fm: dict, cat: dict, src: dict, lotos: dict) -> tuple[str, str]:
    system = (GEN / "prompts" / "sop_gen_v1.system.md").read_text(encoding="utf-8")
    tmpl = (GEN / "prompts" / "sop_gen_v1.user.md").read_text(encoding="utf-8")
    fm_yaml = yaml.safe_dump({fm_id: fm}, allow_unicode=True, sort_keys=False, width=200)
    machines = "\n".join(f"- {t}：{MACHINE_TYPE_LABEL[t]}" for t in fm["machine_types"])
    loto_summary = []
    for lid in loto_ids_for(fm["machine_types"]):
        l = lotos[lid]
        es = "；".join(f"{e['id']} {e['type']}：{e['device']}" for e in l["energy_sources"])
        loto_summary.append(f"- {lid}（{l['title']}）能源：{es}")
    distinguish = "\n".join(f"- {d}：{cat[d]['label']} —— criteria：{cat[d]['criteria']}（{cat[d]['sop']}）"
                            for d in fm["distinguish_from"]) or "（無）"
    targets = sorted({cat[d]["sop"] for d in fm["distinguish_from"]} |
                     {v["sop"] for k, v in cat.items() if k != fm_id and set(v["machine_types"]) & set(fm["machine_types"])})
    sop_labels = {v["sop"]: v["label"] for v in cat.values()}
    escalate_targets = "\n".join(f"- {s}：{sop_labels[s]}" for s in targets)
    srcs = "\n".join(f"- {k}：{v['title']}（授權 {v['license']}，用法 {v['use']}）" for k, v in src.items()
                     if k.startswith(("L", "S")))
    user = tmpl.format(fm_id=fm_id, fm_yaml=fm_yaml, machines=machines, loto_summary="\n".join(loto_summary),
                       distinguish=distinguish, escalate_targets=escalate_targets, sources=srcs)
    return system, user


def draft(only: str | None, force: bool) -> None:
    from llm import MODEL, chat_json

    cat, src, lotos = catalog(), sources(), loto_files()
    RAW.mkdir(parents=True, exist_ok=True)
    jobs = [(k, v) for k, v in cat.items() if not v["safety_only_sop"] and (only is None or k == only)]

    def run(item):
        fm_id, fm = item
        p = RAW / f"{fm['sop']}.json"
        if p.exists() and not force:
            return fm_id, "cached"
        system, user = build_prompt(fm_id, fm, cat, src, lotos)
        seed = int(re.sub(r"\D", "", fm["sop"]))
        try:
            out = chat_json(system, user, SCHEMA, temperature=0.4, seed=seed, retries=5)
        except Exception as e:  # noqa: BLE001
            return fm_id, f"FAILED {e}"
        p.write_text(json.dumps({"generator": {"model": MODEL, "prompt_ver": PROMPT_VER, "seed": seed},
                                 "failure_mode_id": fm_id, "output": out}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return fm_id, "ok"

    with cf.ThreadPoolExecutor(6) as ex:
        for fm_id, status in ex.map(run, jobs):
            print(fm_id, status)


# ---------------------------------------------------------------- assemble
def step(no, phase, action, *, criteria=None, caution=None, sup=False, energized=False, ref=None):
    d = {"no": no, "phase": phase, "action": action}
    if criteria:
        d["criteria"] = criteria
    d["caution"] = caution
    d["requires_supervisor"] = sup
    d["requires_energized"] = energized
    d["ref"] = ref or ["none"]
    return d


def safety_only(fm_id: str, fm: dict) -> dict:
    lid = primary_loto(fm)
    loto_txt = loto_phrase(fm)
    if fm_id == "clutch_brake_wear":
        title = "離合器／制動器異常（停止角度超限）之停機與通報"
        steps = [
            ("prepare", "立即停止生產：滑塊已停止時保持停止；若滑塊無法在正常位置停止，按緊急停止。不得重置停止角度超限警報後繼續生產",
             "停止角度超限代表滑塊可能無法在設計位置停止，任何人不得把手伸入模具區", False, ["REG-N0060034-38", "REG-N0060034-20"]),
            ("prepare", "所有人員離開危險區；通知現場主管與設備工程師，在 Kanban 標記「安全裝置異常」並記錄停止角度、停止時間與氣源壓力讀值",
             None, True, ["REG-N0060001-18"]),
            ("isolate", f"由現場主管指派人員{loto_txt}，並在操作盤掛「安全裝置異常・禁止使用」標籤", "制動器異常時滑塊可能自行下滑：任何人身體不得進入滑塊下方；安全塊由合格人員在主電源隔離、飛輪完全停止後放入，平衡缸與制動器氣源要等安全塊放妥後才洩壓（先洩壓會讓滑塊重量全壓在可疑的制動器上）", True,
             ["REG-N0060009-57-p1", "REG-N0060009-69", "REG-OSHA-1910.147-d-4"]),
            ("inspect", "等待合格人員（設備工程師或原廠技師）依職業安全衛生管理辦法第26條（離合器及制動裝置、一行程一停止機構及緊急制動器）與機械設備器具安全標準第35、38條（氣壓離合器式曲軸沖床的彈簧緊固型制動器、超限運轉監視裝置）進行檢查。一般技術員不得自行調整離合器、制動器或停止角度監視器；AI 不提供排除方法",
             None, True, ["REG-N0060027-26", "REG-N0060034-35", "REG-N0060034-38"]),
            ("verify", "合格人員完成檢查並留下紀錄（管理辦法第80條，保存三年）；現場主管確認停止角度與停止時間量測回到設定範圍內後，才在 Kanban 簽核",
             None, True, ["REG-N0060027-80"]),
            ("restore", f"依 {lid} 解除程序，由原上鎖人親自取下自己的鎖具與標籤（參考 OSHA 1910.147(e)(3)）；送電前確認作業人員無感電之虞（設施規則第254條第2項）；AI 不得宣告可以復電", None, True,
             ["REG-OSHA-1910.147-e-3", "REG-N0060009-254-p2"]),
        ]
        escalate = ["停止角度超限時有人員在危險區或已有人受傷 → 依 S3 處理：急救優先、保全現場，協助主管於 8 小時內通報（職安法第37條）",
                    "氣源壓力同時低於下限 → 由主管確認後另開 SOP-STP-055 工單（氣源壓力不足），但離合器／制動器檢查仍須由合格人員完成",
                    "停止角度在數日內持續增加 → 通知設備工程師／原廠技師安排離合器與制動器定期檢查（管理辦法第26條）"]
        symptoms = ["停止角度超限警報", "滑塊停止位置偏移", "停止時間變長", "制動異音", "寸動時滑塊滑行"]
    else:
        title = "光幕異常或停用中作業之停機與通報"
        steps = [
            ("prepare", "機台保持停止；不得嘗試重置、遮蔽、移動或調整光幕，也不得改用其他操作模式繼續生產", "光幕屬法定安全裝置（機械設備器具安全標準第11條之1、第12條），AI 不提供任何排除方法", False,
             ["REG-N0060034-11-1", "REG-N0060034-12-p1"]),
            ("prepare", "若影像偵測到有人在光幕停用狀態下位於危險區：立即請人員退出危險區至安全位置（職安法第18條），並通知現場主管", "以人員安全為優先，先退避再處理機台", True,
             ["REG-N0060001-18"]),
            ("isolate", f"由現場主管指派人員{loto_txt}，並在操作盤掛「安全裝置異常・禁止使用」標籤", "合格人員需在上下模之間作業時，須先放入安全塊（設施規則第69條）", True,
             ["REG-N0060009-57-p1", "REG-N0060009-69", "REG-OSHA-1910.147-d-4"]),
            ("inspect", "交由現場主管與合格人員（電氣合格人員或原廠技師）確認光幕狀態、自我診斷結果與停用原因；一般技術員與 AI 只負責記錄時間、畫面與狀態，不做排除",
             None, True, ["REG-N0060034-4", "REG-N0060034-6"]),
            ("verify", "合格人員完成檢修後，以測試棒遮光確認滑塊會停止，並留下紀錄；若屬人為停用，由主管了解原因並記錄（究責不究罰），確認後在 Kanban 簽核",
             None, True, ["REG-N0060034-12-p1", "REG-N0060027-59"]),
            ("restore", f"依 {lid} 解除程序，由原上鎖人親自取下自己的鎖具與標籤（參考 OSHA 1910.147(e)(3)）；送電前確認作業人員無感電之虞（設施規則第254條第2項）；AI 不得宣告可以復電", None, True,
             ["REG-OSHA-1910.147-e-3", "REG-N0060009-254-p2"]),
        ]
        escalate = ["光幕停用狀態下偵測到人員在危險區 → 依 S3 處理，主管到場並保全影像紀錄",
                    "有人員受傷 → 急救優先、保全現場，協助主管於 8 小時內通報（職安法第37條）",
                    "同一光幕一週內重複異常 → 通知設備工程師／原廠技師安排檢查"]
        symptoms = ["光幕自我診斷錯誤無法起動", "頻繁誤遮斷停機", "光幕停用中有人在危險區"]
    out_steps = []
    for i, (phase, action, caution, sup, ref) in enumerate(steps, 1):
        out_steps.append(step(i, phase, action, caution=caution, sup=sup, ref=ref))
    return {"title": title, "symptoms": symptoms, "steps": out_steps, "escalate_if": escalate,
            "prerequisites": {"tools": ["Kanban 工單", "「安全裝置異常・禁止使用」標籤", "個人鎖與標籤"], "parts": [],
                              "skill_level": "現場主管／合格人員"},
            "ppe": [], "lessons": "安全裝置異常只做停機、通知、上鎖與記錄，排除交給合格人員。",
            "generator": {"model": None, "prompt_ver": "safety-template-v1", "seed": None}}


def apply_patches(sop_id: str, body: dict) -> dict:
    """Apply reviewed fixes from sop_patches.yaml; warn on patches that no longer match."""
    pt = load_yaml(GEN / "sop_patches.yaml")
    s = json.dumps(body, ensure_ascii=False)
    for a, b in pt.get("global_replace", []):
        s = s.replace(a, b)
    per = (pt.get("per_sop") or {}).get(sop_id, {})
    for a, b in per.get("replace", []):
        if a not in s:
            print(f"  WARN {sop_id}: patch not matched: {a[:30]}")
        s = s.replace(a, b)
    body = json.loads(s)
    for item in per.get("set", []):
        parts = item["path"].split(".")
        if len(parts) == 1:  # top-level field, e.g. isolate_caution
            body[parts[0]] = item["value"]
            continue
        ph, idx, field = parts
        body[ph][int(idx)][field] = item["value"]
    return body


def review_meta() -> dict:
    """Review metadata (status / reviewer / note) recorded in sop_patches.yaml → applied to every assembled SOP."""
    r = load_yaml(GEN / "sop_patches.yaml").get("review") or {}
    return {"review_status": r.get("status", "draft"), "reviewed_by": list(r.get("reviewed_by") or []),
            "review_note": r.get("review_note"), "review_date": r.get("review_date"),
            "review_doc": r.get("review_doc")}


# 試沖／連續生產前的恢復確認（review 2026-10-21 press chief）：測試／定位程序（OSHA 1910.147(f)(1)）只涵蓋
# 短暫通電的寸動調整與讀值；用料帶試沖、連續生產屬於恢復生產，必須先恢復護圍、光幕與安全裝置並撤出安全塊。
TRIAL_KEYWORDS = ("試沖", "生產", "連續", "空運轉")
PRE_TRIAL = ("試沖與連續生產屬於恢復生產，不是測試／定位：先清點工具、撤出安全塊、恢復護圍、光幕與所有安全裝置並確認功能正常、"
             "確認人員離開危險區與滾輪夾捲點；由原上鎖人取下自己的鎖（連動上鎖的送料機／沖床一併確認）後，才由操作員以正常操作模式"
             "（單動、光幕與雙手操作有效）試沖。試沖中發現異常，須重新依 LOTO 上鎖後才能再進入模區或滾輪夾捲點")


def assemble_mode(fm_id: str, fm: dict, cat: dict, src: dict) -> dict:
    lid = primary_loto(fm)
    loto_ids = all_loto(fm)
    lotos = loto_files()
    if fm["safety_only_sop"]:
        body = safety_only(fm_id, fm)
        steps = body["steps"]
        gen = body["generator"]
    else:
        raw = json.loads((RAW / f"{fm['sop']}.json").read_text(encoding="utf-8"))
        body, gen = apply_patches(fm["sop"], raw["output"]), raw["generator"]
        steps = []

        def add(phase, action, **kw):
            steps.append(step(len(steps) + 1, phase, action, **kw))

        add("prepare", "通知操作員與班長停線，於 Kanban 開立維修單；記錄停機當下的警報碼與近 15 分鐘訊號趨勢"
            "（之後判讀趨勢用 MES／Sentinel 已記錄的資料，不需要為了看紀錄對機台送電）",
            ref=["REG-OSHA-1910.147-d-1"])
        add("prepare", "確認作業許可已由現場主管核准（許可單號；許可單列出本次隔離點），核准後技術員才依 LOTO 檢查表隔離"
            "（廠內程序；危害防止計畫須含作業許可程序係第57條修正條文，116年1月1日施行）", sup=True,
            ref=["REG-N0060009-57-p1"])
        add("isolate", loto_phrase(fm), caution=body["isolate_caution"], sup=True,
            ref=["REG-N0060009-57-p1", "REG-N0060009-57-v20240801", "REG-OSHA-1910.147-d-6"])
        if fm_id in DIE_SPACE:
            add("isolate", "手要進入上下模之間前：滑塊停在上死點（開模位置），於上下模之間放入安全塊（設施規則第69條）；"
                "滑塊停在下死點附近、無法退回時不得進入模區，保持上鎖並通知設備工程師",
                caution="安全塊放置位置與規格依模具資料；未放安全塊不得作業。平衡缸要等安全塊放妥後才洩壓；"
                        "制動器有疑慮（停止角度變大、寸動會滑行）時，放安全塊前先通知設備工程師",
                sup=False, ref=["REG-N0060009-69", "REG-OSHA-1910.147-d-5"])
        energized_announced = False
        trial_announced = False
        for phase in ("inspect", "repair", "verify"):
            for s in body[phase]:
                if (phase == "verify" and not trial_announced and s.get("requires_energized")
                        and any(k in s["action"] for k in TRIAL_KEYWORDS)):
                    add("verify", PRE_TRIAL, caution="AI 不得宣告可以復電或可以生產；是否恢復試沖由現場主管決定", sup=True,
                        ref=["REG-OSHA-1910.147-e-1", "REG-OSHA-1910.147-e-2", "REG-OSHA-1910.147-e-3", "REG-N0060009-254-p2"])
                    trial_announced = True
                if s.get("requires_energized") and not energized_announced:
                    tp = "；".join(lotos[lid]["testing_positioning"])
                    add(phase, f"需要通電量測或移動滑塊時，依 {lid} 測試／定位程序：{tp}",
                        caution="通電期間手不得進入模具區或滾輪夾捲點；測試／定位只限寸動調整與讀值，不得用來試沖或連續生產", sup=True,
                        ref=["REG-OSHA-1910.147-f-1", "REG-N0060009-70"])
                    energized_announced = True
                if phase == "repair" and fm_id in DIE_ADJUST and not any("寸動" in x["action"] for x in steps):
                    add("repair", "調整金屬模需要讓滑塊動作時，一律採用寸動；未具寸動機構時切斷動力電源後再調整（設施規則第70條）",
                        sup=False, ref=["REG-N0060009-70", "REG-N0060034-22"])
                add(phase, s["action"], criteria=s.get("criteria"), caution=s.get("caution"),
                    sup=bool(s.get("requires_supervisor", False)), energized=bool(s.get("requires_energized")),
                    ref=s.get("ref") or ["none"])
                if s.get("requires_energized") and phase != "verify":
                    pass
        add("restore", f"依 {lid} 解除程序（連動上鎖的送料機／沖床依各自檢查表一併解除）：清點工具、恢復護圍與光幕、取出安全塊、確認人員離開後，"
            "由原上鎖人親自取下自己的鎖具與標籤（參考 OSHA 1910.147(e)(3)）；原上鎖人不在場時不得由他人剪鎖或代取，依廠內書面異常解鎖程序由主管處理（同條 (e)(3) 例外規定）；"
            "送電前確認作業人員無感電之虞，並拆除短路接地器具（如有使用）與標示（設施規則第254條第2項）；AI 不得宣告可以復電",
            sup=True, ref=["REG-OSHA-1910.147-e-3", "REG-N0060009-254-p2"])
        add("restore", "在 Kanban 填寫實際原因、處置內容與處置後是否恢復正常（J5 結案回寫）", ref=["none"])
    verify = [s["action"].rstrip("。") + (f"；判定：{s['criteria']}" if s.get("criteria") else "") for s in steps if s["phase"] == "verify"]
    restore = [s["action"] for s in steps if s["phase"] == "restore"]
    ppe = ["安全鞋", "防割手套", "護目鏡", "耳塞（設施規則第300條）"]
    for p in body.get("ppe", []):
        if p and not any(p in x or x in p for x in ppe):
            ppe.append(p)
    legal = ["設施規則第57條", "設施規則第254條（電氣送電前確認）", "OSHA 1910.147(d)", "OSHA 1910.147(e)(3)（原上鎖人取下鎖具）"]
    if fm_id in DIE_SPACE or fm["safety_only_sop"]:
        legal.insert(1, "設施規則第69條")
    if fm_id in DIE_ADJUST:
        legal.insert(2, "設施規則第70條")
    if fm_id == "clutch_brake_wear":
        legal += ["職業安全衛生管理辦法第26條", "機械設備器具安全標準第35、38條"]
    if fm_id == "light_curtain_fault":
        legal += ["機械設備器具安全標準第11條之1、第12條", "職業安全衛生法第18條"]
    energy = []
    for l in loto_ids_for(fm["machine_types"]):
        for e in lotos[l]["energy_sources"]:
            if e["type"] not in energy:
                energy.append(e["type"])
    refs, used = [], set()
    reg = {c: True for c in []}
    for s in steps:
        for r in s["ref"]:
            used.add(r)
    for code in fm["sources"] + sorted(x for x in used if x in src and x not in fm["sources"]):
        v = src[code]
        refs.append({"id": code, "title": v["title"], "url": v["url"], "license": v["license"],
                     "use": "paraphrase" if v["license"].startswith("©") else ("quote" if v["use"] == "ingest" else "reference")})
    regs = sorted(x for x in used if x.startswith("REG-"))
    if regs:
        refs.append({"id": "public/regulations.jsonl", "title": "法規條文 chunk：" + "、".join(regs),
                     "url": "https://law.moj.gov.tw/ ; https://www.ecfr.gov/", "license": "TW-law / PD", "use": "quote"})
    symptoms = list(dict.fromkeys(fm["symptoms"] + body["symptoms"]))
    doc = {
        "sop_id": fm["sop"], "version": "0.1", "title": body["title"], "line": LINE,
        "machine_type": fm["machine_types"], "failure_mode_ids": [fm_id],
        "data_label": "模擬資料", "synthetic": True, "author": "team_sentinel",
        **{k: v for k, v in review_meta().items() if v is not None}, "category": fm["category"], "safety_only": fm["safety_only_sop"],
        "generator": gen,
        "safety": {"loto_required": fm["loto_required"], "loto_checklist_id": lid, "loto_checklist_ids": loto_ids,
                   "energy_sources": energy, "ppe": ppe, "danger_zone_entry": True, "legal_basis": legal},
        "symptoms": symptoms,
        "prerequisites": body["prerequisites"],
        "steps": steps,
        "verify": verify,
        "restore": restore,
        "escalate_if": body["escalate_if"],
        "lessons": body.get("lessons"),
        "references": refs,
    }
    return doc


def fullwidth(s):
    if not isinstance(s, str):
        return s
    s = re.sub(r"(?<=[一-鿿）」]),\s*", "，", s)
    s = re.sub(r"(?<=[一-鿿）」]);\s*", "；", s)
    s = re.sub(r"(?<=[一-鿿]):\s*", "：", s)
    s = re.sub(r"\((?=[一-鿿])", "（", s)
    s = re.sub(r"(?<=[一-鿿])\)", "）", s)
    return s


def deep(o, f):
    if isinstance(o, dict):
        return {k: (deep(v, f) if k not in ("url", "ref", "id", "sop_id") else v) for k, v in o.items()}
    if isinstance(o, list):
        return [deep(v, f) for v in o]
    return f(o)


class Dumper(yaml.SafeDumper):
    pass


def _str(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


Dumper.add_representer(str, _str)


def assemble() -> None:
    cat, src = catalog(), sources()
    OUT.mkdir(parents=True, exist_ok=True)
    for fm_id, fm in cat.items():
        doc = deep(assemble_mode(fm_id, fm, cat, src), fullwidth)
        text = yaml.dump(doc, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=1000)
        header = (f"# {doc['sop_id']} {doc['title']}\n# data_label: 模擬資料 —— 團隊依公開法規與公開技術文件撰寫之模擬 SOP，"
                  "與任何企業實際製程無關；數值門檻皆為模擬值。"
                  + (f"review_status: {doc['review_status']}（{doc['review_note']}）\n" if doc.get("review_note")
                     else f"review_status: {doc['review_status']}（待人工審核）\n"))
        (OUT / f"{doc['sop_id']}.yaml").write_text(header + text, encoding="utf-8")
        bad = [w for w in SAFETY_BLACKLIST if w in json.dumps(doc["steps"], ensure_ascii=False)]
        print(doc["sop_id"], fm_id, len(doc["steps"]), "steps", ("BLACKLIST:" + ",".join(bad)) if bad else "")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["draft", "assemble"])
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(GEN))
    draft(a.only, a.force) if a.cmd == "draft" else assemble()
