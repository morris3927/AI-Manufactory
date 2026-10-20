"""Generate synthetic welding / assembly repair cases (knowledge/cases/{line}/CASE-{WLD,ASM}-*.json).

Same pipeline as gen_cases.py (stamping, untouched):
  1. skeleton — case_skeleton_wa.Planner(line) decides all facts deterministically
  2. draft    — DeepSeek V4.1 Flash writes only the text fields (cached in _gen/raw/cases/{line}/)
  3. assemble — merge skeleton + text, insert LOTO / testing-positioning / restore / hand-over steps,
                apply case_patches_weld_asm.yaml, automatic checks, held-out split (1 per mode = 10).
                Files are rewritten in place (no delete-then-write → no iCloud "X 2.json" conflict copies).

Usage:
  python3.13 gen_cases_wa.py draft --line welding [--ids 3,17] [--force]
  python3.13 gen_cases_wa.py assemble --line welding
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import re
import sys

from case_skeleton_wa import SEED, Planner
from common import GEN, KB, SAFETY_BLACKLIST, catalog, load_yaml
from gen_cases import SCHEMA, apply_text_patch, check, draft_hash, jaccard
from lines import LINES

PROMPT_VER = "case-gen-wa-v1"
PATCHES = GEN / "case_patches_weld_asm.yaml"

STYLE = {
    "welding": {
        "abbrev": "RC01、RC02、SP01、SP02",
        "typos": "「銲／焊」混用、「導電咀」「送絲論」",
        "synonyms": "送絲不順／送絲卡／鳥巢；導電嘴／導電咀／contact tip；噴嘴／噴頭；氣孔／砂孔／針孔；飛濺／噴渣；燒穿／燒破；接地夾／地線夾；TCP 偏／槍偏；電極帽／電極頭／cap",
        "examples": ["RC01 斷弧+送絲卡 開送絲機看到鳥巢 剪掉重穿 導管吹乾淨 試銲ok",
                     "RC02 起弧一直重試 導電嘴孔變大 換新 重試次數降回1 ok",
                     "SP01 熔核小 剝離不過 cap 6.8mm 換cap修磨 試片ok",
                     "RC01 排煙停了 已停銲通知廠務 等廠務修 上鎖中"],
        "rules": ("   - 機器人工作站：進入圍欄前已上鎖並掛禁止觸動；移動機器人只能寫「教導模式降速」；試銲寫成在局部排氣運轉下、人員在圍欄外進行。\n"
                  "   - 點銲機：手不得放在上下電極之間；量加壓力或對心寫「不通電加壓」。\n"
                  "   - 銲接工位 ROI 內的火花與銲煙是正常的；機器人停止後仍冒煙或工位外有火光要寫成「通報緊急應變」，不是維修。"),
    },
    "assembly": {
        "abbrev": "ST01、ST02、MA01、MA02",
        "typos": "「起子頭／起子投」「滑牙／花牙」「卡扣／卡釦」",
        "synonyms": "浮鎖／沒鎖平／浮高；滑牙／花牙／崩牙；斜牙／歪牙；起子頭／bit／批頭；打滑／cam out；送釘機／供釘機；漏鎖／少打一顆；夾線／壓線；連接器／接頭／connector；卡扣／卡榫",
        "examples": ["ST01 H07一直NOK 初段扭力就衝高 孔牙有鋅渣 清孔換新螺絲 ok",
                     "ST02 打滑聲 bit 4萬5次了 十字槽磨花 換bit 計數歸零 ok",
                     "MA01 測試抓不到硬碟 SATA卡扣沒扣 重插 測3次pass",
                     "ST01 PPE告警 手套破了 班長補發 已提醒 ok"],
        "rules": ("   - 換起子頭、清卡釘、拆蓋檢查線材前已依 LOTO-ASM-TOOL-01 拔插頭（或取下電池）、關送釘機氣源；試鎖只用試片或報廢件。\n"
                  "   - 發現不良要寫可疑工件隔離、追溯與首件確認；不得寫放寬扭力上下限或關閉鎖附計數。"),
    },
}
JOG_RE = {"welding": re.compile(r"試銲|試點銲|空送絲|送絲測試|教導|寸動|通電|量測流量|量流量|試打|空跑|TCP 校"),
          "assembly": re.compile(r"試鎖|試打|扭力抽測|抽測|功能測試|測試站|通電|重測|復測")}
TRIAL_RE = re.compile(r"恢復生產|恢復量產|量產|復機|復線|連續生產|恢復作業")
RECORD_RE = re.compile(r"^(調閱|查閱|讀取|比對|核對|查看)")
HANDS_ON = {"welding": re.compile(r"拆|更換|剪|清潔噴嘴|清槍|圍欄內|電極之間|換cap|換新"),
            "assembly": re.compile(r"拆|更換|清卡釘|送釘軌道|開蓋|換bit|換起子頭|重插|重新插")}
TEST_STEP = {
    "welding": "通電測試前依 {loto} 測試／定位程序：人員退到圍欄外或手離開電極區，原上鎖人取下自己的鎖、主管在場，機器人只用教導模式降速；試銲前確認局部排氣運轉；測試後若要再進入須重新上鎖",
    "assembly": "試鎖／測試前依 {loto} 測試／定位程序：原上鎖人取下自己的插頭鎖蓋插回電源，只用試片或報廢件，手指不放在起子頭下方；測試後若要再拆修須重新上鎖",
}
RESTORE_STEP = {
    "welding": "恢復生產前確認：清點工具、恢復安全門連鎖與護罩、確認人員離開機器人可動範圍與電極區，由原上鎖人取下自己的鎖後以正常模式復機",
    "assembly": "恢復量產前確認：清點工具與螺絲、恢復送釘機護蓋，由原上鎖人取下自己的插頭鎖蓋並插回電源，首件由班長確認",
}
HANDOVER_STEP = ("設備維持上鎖掛牌與「禁止操作」標示待修；移交時由接手人員掛上自己的鎖後，原上鎖人才取下自己的鎖"
                 "（OSHA 1910.147(f)(4) 換班移交，模擬作業規定）")


def raw_dir(line):
    return GEN / "raw" / "cases" / line


def case_id(line, sk):
    return f"CASE-{LINES[line]['prefix']}-{sk['idx'] + 1:04d}"


def skeletons(line):
    P = Planner(line)
    return [P.build(i, p) for i, p in enumerate(P.plan())]


def sop_summary(line, sop_id):
    p = KB / "sop" / line / f"{sop_id}.yaml"
    if not p.exists():
        return ""
    d = load_yaml(p)
    acts = [s["action"][:60] for s in d["steps"] if s["phase"] in ("inspect", "repair") and "測試／定位程序" not in s["action"]]
    return "；".join(acts[:7])


def system_prompt(line):
    st = STYLE[line]
    return (GEN / "prompts" / "case_gen_wa_v1.system.md").read_text(encoding="utf-8").format(
        context=LINES[line]["context"], typos=st["typos"], abbrev=st["abbrev"], synonyms=st["synonyms"],
        line_rules=st["rules"], examples="\n".join(f"- {x}" for x in st["examples"]))


def build_user(line, sk, cat):
    fm = cat[sk["fm_id"]]
    first = sk["first_hypothesis"]["failure_mode_id"]
    view = {k: sk[k] for k in ("case_type", "machine_id", "machine_type", "opened_at", "closed_at", "shift", "trigger",
                               "context", "log_signals", "first_hypothesis", "final_root_cause", "contributing_causes",
                               "hypothesis_correct", "outcome", "downtime_min", "parts_vocab", "technician", "safety_flags",
                               "escalated_to", "new_mode", "false_alarm_story")}
    out = ["## 骨架（事實，不可更改）", "```json", json.dumps(view, ensure_ascii=False, indent=1), "```",
           f"## 真正根因所屬模式：{sk['fm_id']}（{fm['label']}，category={fm['category']}）",
           f"criteria：{fm['criteria']}", f"常見症狀：{'、'.join(fm['symptoms'])}",
           f"對應 SOP {fm['sop']} 的檢查與修復重點：{sop_summary(line, fm['sop'])}"]
    if first != sk["fm_id"] and first in cat:
        out += [f"## 第一判斷的模式：{first}（{cat[first]['label']}）", f"criteria：{cat[first]['criteria']}"]
    for c in sk["contributing_causes"]:
        out += [f"## 同時存在的次要原因：{c}（{cat[c]['label']}）", f"criteria：{cat[c]['criteria']}"]
    if sk["case_type"] == "false_alarm":
        out += [f"## 這是誤報：{sk['false_alarm_story']}。final_root_cause=none，檢查後無故障。"]
    if sk["case_type"] == "new":
        out += [f"## 這是目錄外的新故障模式：{sk['new_mode']['description']}。一開始被當成 {first}，"
                f"照 SOP 處理無效，最後查出是上述新原因，移交 {sk['escalated_to']}，final_root_cause=new。"]
    if sk["case_type"] == "escalated":
        out += [f"## 根因已確認是 {sk['fm_id']}，但現場無法完成修復，移交 {sk['escalated_to']}。"]
    if sk["case_type"] == "composite":
        out += ["## 這是複合原因：兩個原因同時存在，actions 要兩個都處理到，normalized 要說明主因與次因。"]
    out.append("請依 system 規則輸出 JSON。")
    return "\n".join(out)


def draft(line, ids, force):
    from llm import MODEL, chat_json

    cat = catalog(line)
    system = system_prompt(line)
    raw = raw_dir(line)
    raw.mkdir(parents=True, exist_ok=True)
    sks = [s for s in skeletons(line) if ids is None or s["idx"] + 1 in ids]

    def run(sk):
        cid = case_id(line, sk)
        p = raw / f"{cid}.json"
        if p.exists() and not force:
            return cid, "cached"
        seed = (3000 if line == "welding" else 4000) + sk["idx"]
        try:
            out = chat_json(system, build_user(line, sk, cat), SCHEMA, temperature=0.8, seed=seed, retries=5)
        except Exception as e:  # noqa: BLE001
            return cid, f"FAILED {e}"
        p.write_text(json.dumps({"generator": {"model": MODEL, "prompt_ver": PROMPT_VER, "seed": seed}, "output": out},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        return cid, "ok"

    with cf.ThreadPoolExecutor(8) as ex:
        for cid, st in ex.map(run, sks):
            if st != "cached":
                print(cid, st, flush=True)


def insert_energy_steps(line, actions, li, loto):
    acts = list(actions)
    first_jog = first_trial = None
    for i in range(li + 1, len(acts)):
        st = acts[i]["step"]
        if TRIAL_RE.search(st) and first_trial is None:
            first_trial = i
            break
        if JOG_RE[line].search(st) and first_jog is None and not RECORD_RE.search(st):
            first_jog = i
    if first_trial is not None:
        acts.insert(first_trial, {"step": RESTORE_STEP[line], "result": "ok"})
    if first_jog is not None and (first_trial is None or first_jog < first_trial):
        acts.insert(first_jog, {"step": TEST_STEP[line].format(loto=loto), "result": "ok"})
    return acts


def write_in_place(fp, txt):
    if not fp.exists() or fp.read_text(encoding="utf-8") != txt:
        fp.write_text(txt, encoding="utf-8")


def assemble(line):
    cfg = LINES[line]
    pre = cfg["prefix"]
    cat = catalog(line)
    sks = skeletons(line)
    out_dir = KB / "cases" / line
    held_dir = out_dir / "heldout"
    out_dir.mkdir(parents=True, exist_ok=True)
    held_dir.mkdir(parents=True, exist_ok=True)
    pt = (load_yaml(PATCHES) or {}) if PATCHES.exists() else {}
    patches = pt.get("cases") or {}
    review = pt.get("review") or {}
    rnd = random.Random(SEED[line] + 1)
    want = ["wrong_first"] * 2 + ["false_alarm", "new", "escalated", "composite"] + ["typical"] * 4
    rnd.shuffle(want)
    held, by_mode = set(), {}
    for s in sks:
        by_mode.setdefault(s["fm_id"], []).append(s)
    for i, fm_id in enumerate(cat):
        pool = [s for s in by_mode[fm_id] if s["case_type"] == want[i]] or [s for s in by_mode[fm_id] if s["case_type"] == "typical"]
        held.add(rnd.choice(pool)["idx"])
    problems, docs = {}, []
    for sk in sks:
        cid = case_id(line, sk)
        p = raw_dir(line) / f"{cid}.json"
        if not p.exists():
            problems[cid] = ["missing draft"]
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        patch = patches.get(cid) or {}
        pwarn = []
        out = apply_text_patch(cid, raw["output"], patch, pwarn)
        fm = cat[sk["fm_id"]]
        from case_skeleton_wa import PARTS
        for cc in sk["contributing_causes"]:  # composite: the secondary cause's parts are also legitimate
            sk["parts_vocab"] = list(dict.fromkeys(sk["parts_vocab"] + PARTS[line][cc]))
        actions = list(out["actions"])
        txt = " ".join(a["step"] for a in actions)
        if fm["loto_required"] and not sk["safety_flags"]["loto_done"] and HANDS_ON[line].search(txt):
            # program rule (reviewer 2026-10-21): any hands-on step means the person entered → LOTO mandatory
            sk["safety_flags"] = {**sk["safety_flags"], "loto_done": True, "danger_zone_entry": True}
            pwarn.append("hands-on in false alarm → loto forced")
        errs = [e for e in check(sk, out, cat) + pwarn if not e.startswith("hands-on")]
        if errs:
            problems[cid] = errs
        loto = cfg["loto_by_type"][sk["machine_type"]]
        if sk["safety_flags"]["loto_done"]:
            step0 = (f"依 {loto} 拔插頭／取下電池、關送釘機氣源並洩壓、掛個人標籤，驗證不動作" if line == "assembly"
                     else f"依 {loto} 停機、上鎖掛牌、啟動開關掛「禁止觸動」，驗證零能量")
            actions.insert(0, {"step": step0, "result": "ok"})
            actions = insert_energy_steps(line, actions, 0, loto)
            if sk["outcome"] == "escalated":
                actions.append({"step": HANDOVER_STEP, "result": "保持隔離"})
        related = [x for x in dict.fromkeys([sk["final_root_cause"], *sk["contributing_causes"],
                                             sk["first_hypothesis"]["failure_mode_id"], sk["fm_id"]]) if x in cat]
        doc = {
            "case_id": cid, "data_label": "模擬資料", "synthetic": True,
            "generator": {**raw["generator"], "skeleton": f"case_skeleton_wa.Planner('{line}', seed={SEED[line]})"},
            "split": "heldout" if sk["idx"] in held else "train",
            "case_type": sk["case_type"], "line": line, "machine_type": sk["machine_type"], "machine_id": sk["machine_id"],
            "opened_at": sk["opened_at"], "closed_at": sk["closed_at"], "shift": sk["shift"], "trigger": sk["trigger"],
            "context": {k: v for k, v in sk["context"].items() if k != "season"},
            "log_signals": sk["log_signals"], "technician": sk["technician"],
            "technician_report_raw": out["technician_report_raw"].strip(),
            "technician_report_normalized": out["technician_report_normalized"].strip(),
            "first_hypothesis": sk["first_hypothesis"], "final_root_cause": sk["final_root_cause"],
            "contributing_causes": sk["contributing_causes"], "hypothesis_correct": sk["hypothesis_correct"],
            "why_first_guess_wrong": out["why_first_guess_wrong"] if not sk["hypothesis_correct"] else None,
            "discriminating_evidence": out["discriminating_evidence"] if not sk["hypothesis_correct"] else [],
            "new_mode_proposal": sk["new_mode"], "actions": actions, "sop_used": sk["sop_used"],
            "downtime_min": sk["downtime_min"], "parts_replaced": out["parts_replaced"], "safety_flags": sk["safety_flags"],
            "outcome": sk["outcome"], "escalated_to": sk["escalated_to"],
            "escalation_note": out.get("escalation_note") if sk["outcome"] == "escalated" else None,
            "lessons": out["lessons"].strip(), "related_failure_mode_ids": related, "category": fm["category"],
            "references": sk["sop_used"], "review_status": "draft",
        }
        if review.get("status") == "approved" and (review.get("hashes") or {}).get(cid) == draft_hash(raw["output"]):
            doc.update({"review_status": "approved", "reviewed_by": review["reviewed_by"], "review_note": review["note"],
                        "review_doc": review.get("doc")})
        elif review.get("status") == "approved":
            problems.setdefault(cid, []).append("approval stale or missing → left as draft")
        docs.append(doc)
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if a["related_failure_mode_ids"][:1] == b["related_failure_mode_ids"][:1]:
                j = jaccard(a["technician_report_raw"], b["technician_report_raw"])
                if j > 0.6:
                    problems.setdefault(b["case_id"], []).append(f"near-duplicate of {a['case_id']} ({j:.2f})")
    keep = set()
    for d in docs:
        fp = (held_dir if d["split"] == "heldout" else out_dir) / f"{d['case_id']}.json"
        keep.add(fp)
        write_in_place(fp, json.dumps(d, ensure_ascii=False, indent=2))
    for old in list(out_dir.glob(f"CASE-{pre}-*.json")) + list(held_dir.glob(f"CASE-{pre}-*.json")):
        if old not in keep:
            old.unlink()
    print(f"{line}: wrote {len(docs)} cases ({sum(d['split'] == 'heldout' for d in docs)} heldout)")
    for k, v in sorted(problems.items()):
        print("  CHECK", k, v)
    (GEN / "raw" / f"case_check_report_{line}.json").write_text(json.dumps(problems, ensure_ascii=False, indent=1), encoding="utf-8")


def approve(line):
    """Pin AI role-play reviewer sign-off to the hash of each reviewed draft (re-review if a draft changes)."""
    import yaml
    pt = (load_yaml(PATCHES) or {}) if PATCHES.exists() else {}
    rv = pt.setdefault("review", {})
    hashes = rv.setdefault("hashes", {})
    for sk in skeletons(line):
        cid = case_id(line, sk)
        hashes[cid] = draft_hash(json.loads((raw_dir(line) / f"{cid}.json").read_text(encoding="utf-8"))["output"])
    rv["hashes"] = dict(sorted(hashes.items()))
    PATCHES.write_text("# 銲接／組裝案例審稿修正與簽核（gen_cases_wa.py assemble 套用；approve 產生 hashes）\n" +
                       yaml.safe_dump(pt, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    print(f"pinned {len(hashes)} hashes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["draft", "assemble", "approve"])
    ap.add_argument("--line", required=True, choices=sorted(LINES))
    ap.add_argument("--ids")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(GEN))
    ids = {int(x) for x in a.ids.split(",")} if a.ids else None
    {"draft": lambda: draft(a.line, ids, a.force), "assemble": lambda: assemble(a.line), "approve": lambda: approve(a.line)}[a.cmd]()
