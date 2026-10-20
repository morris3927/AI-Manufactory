"""Generate synthetic stamping repair cases (knowledge/cases/stamping/CASE-STP-*.json).

Pipeline (07_RAG知識庫資料.md §6.2, §7.1 step 6):
  1. skeleton — case_skeleton.Planner decides all facts deterministically (seed 2026)
  2. draft    — DeepSeek V4.1 Flash writes only the text fields from each skeleton
                (raw JSON saved under _gen/raw/cases/, cached; --force / --ids to redo)
  3. assemble — merge skeleton + text, add the LOTO action, run automatic checks
                (schema, parts vocabulary, blacklist, near-duplicate text), select the
                held-out split (1 case per failure mode = 20) into cases/stamping/heldout/

Usage:
  python3.13 gen_cases.py draft [--ids 3,17] [--force]
  python3.13 gen_cases.py assemble       # applies case_patches.yaml + review sign-off in case_review.yaml
  python3.13 gen_cases.py approve [--ids 3,17]   # pin reviewer sign-off to the current draft hash
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import re
import sys

import hashlib

from case_skeleton import Planner
from common import GEN, KB, LINE, LOTO_BY_TYPE, MACHINES, SAFETY_BLACKLIST, catalog, load_yaml

PROMPT_VER = "case-gen-v1"
SEED = 2026
RAW = GEN / "raw" / "cases"
OUT = KB / "cases" / LINE
HELD = OUT / "heldout"

SCHEMA = {
    "type": "object",
    "properties": {
        "technician_report_raw": {"type": "string"},
        "technician_report_normalized": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "step": {"type": "string"}, "result": {"type": "string"}}, "required": ["step", "result"]}},
        "parts_replaced": {"type": "array", "items": {"type": "string"}},
        "why_first_guess_wrong": {"type": ["string", "null"]},
        "discriminating_evidence": {"type": "array", "items": {"type": "string"}},
        "lessons": {"type": "string"},
        "escalation_note": {"type": ["string", "null"]},
    },
    "required": ["technician_report_raw", "technician_report_normalized", "actions", "parts_replaced",
                 "why_first_guess_wrong", "discriminating_evidence", "lessons", "escalation_note"],
}


PATCHES = GEN / "case_patches.yaml"   # reviewed per-case fixes (re-applied on every assemble)
REVIEW = GEN / "case_review.yaml"     # review sign-off, pinned to a hash of the reviewed draft text

# ---- post-process rules from the 2026-10-21 reliability-engineer review --------------------------
# (a) energised steps after LOTO: testing/positioning (OSHA 1910.147(f)(1)) vs. restoring for trial strokes
JOG_RE = re.compile(r"寸動|空轉|點動|試送|通電|復電|降速|降回|低速送料")
TRIAL_RE = re.compile(r"試沖|試跑|試機|試運轉|連續生產|恢復生產|恢復量產|恢復連續|量產|復機|復線|觀察運轉")
WEAK_RE = re.compile(r"重置|復歸")
RECORD_RE = re.compile(r"^(調閱|查閱|讀取|比對|核對|查看警報|確認 MISFEED)")
TEST_STEP = ("通電測試前依 {loto} 測試／定位程序：清除危險區工具與人員，原上鎖人取下自己的鎖、主管在場，只用寸動；"
             "測試後若要再進入危險區須重新上鎖")
RESTORE_STEP = ("試沖前恢復確認：清點工具、撤出安全塊、恢復護圍與光幕等安全裝置、確認人員離開危險區與滾輪夾捲點，"
                "由原上鎖人取下自己的鎖（含連動上鎖機台）後以正常操作模式試沖；試沖中異常須重新上鎖才能再進入")
HANDOVER_STEP = ("機台維持上鎖掛牌與「禁止操作」標示待修；移交時由接手人員掛上自己的鎖後，原上鎖人才取下自己的鎖"
                 "（OSHA 1910.147(f)(4) 換班移交，模擬作業規定）")
# (b) work that crosses the press/feeder boundary needs the linked LOTO (LOTO-FEED-01 F6 / LOTO-PRESS E5–E6)
DIE_WORK_RE = re.compile(r"模面|沖頭|沖子|衝子|上模|下模|落料孔|脫料板|導正板|導正銷|定位銷|開模|模內|模具定位|跳屑|平衡缸|離合器|制動器")
FEED_WORK_RE = re.compile(r"滾輪|送料機|整平|料架|側導板|FD0\d")
PRESS_OF = {"STP-L1-FD02": "STP-L1-PR01", "STP-L2-FD04": "STP-L2-PR03"}
FEED_OF = {v: k for k, v in PRESS_OF.items()}


def load_patches() -> dict:
    return (load_yaml(PATCHES) or {}).get("cases") or {} if PATCHES.exists() else {}


def draft_hash(out: dict) -> str:
    return hashlib.sha1(json.dumps(out, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def apply_text_patch(cid: str, out: dict, patch: dict, warn: list) -> dict:
    """Reviewed text fixes on top of the cached LLM draft (the cache itself is never edited)."""
    out = json.loads(json.dumps(out, ensure_ascii=False))
    if "actions" in patch:
        out["actions"] = patch["actions"]
    for k, v in (patch.get("set") or {}).items():
        out[k] = v

    def sub(x, a, b, hit):
        if isinstance(x, str):
            if a in x:
                hit[0] = True
            return x.replace(a, b)
        if isinstance(x, list):
            return [sub(i, a, b, hit) for i in x]
        if isinstance(x, dict):
            return {k: sub(v, a, b, hit) for k, v in x.items()}
        return x

    for a, b in patch.get("replace") or []:
        hit = [False]
        out = sub(out, a, b, hit)
        if not hit[0]:
            warn.append(f"patch not matched: {a[:30]}")
    return out


def loto_step(sk: dict, actions: list) -> str:
    m, mtype = sk["machine_id"], sk["machine_type"]
    txt = " ".join(a["step"] + a["result"] for a in actions)
    loto = LOTO_BY_TYPE[mtype]
    if mtype == "servo_roll_feeder" and DIE_WORK_RE.search(txt):
        pr = PRESS_OF[m]
        return f"依 {loto} 停機、上鎖掛牌，下游沖床 {pr[-4:]} 依 {LOTO_BY_TYPE[MACHINES[pr]['type']]} 連動上鎖，驗證零能量"
    if mtype != "servo_roll_feeder" and FEED_WORK_RE.search(txt):
        return f"依 {loto} 停機、上鎖掛牌（送料機 {FEED_OF[m][-4:]} 依 LOTO-FEED-01 連動上鎖），驗證零能量"
    return f"依 {loto} 停機、上鎖掛牌、驗證零能量"


def insert_energy_steps(actions: list, loto_idx: int, loto: str) -> list:
    """After the LOTO step, put the testing/positioning step before the first jog / air-run and the
    restore-for-trial step before the first trial stroke or return to production."""
    acts = list(actions)
    first_jog = first_trial = None
    for i in range(loto_idx + 1, len(acts)):
        st = acts[i]["step"]
        trial = bool(TRIAL_RE.search(st)) or (bool(WEAK_RE.search(st)) and not RECORD_RE.search(st))
        if not trial and TRIAL_RE.search(acts[i]["result"]) and first_trial is None:
            # trial stroke only mentioned in the result → split it out so the restore step sits between
            acts[i:i + 1] = [{"step": st, "result": "完成"}, {"step": "試沖確認", "result": acts[i]["result"]}]
            first_trial = i + 1
            break
        if trial:
            first_trial = i
            break
        if JOG_RE.search(st) and first_jog is None and not RECORD_RE.search(st):
            first_jog = i
    if first_trial is not None:
        acts.insert(first_trial, {"step": RESTORE_STEP, "result": "ok"})
    if first_jog is not None and (first_trial is None or first_jog < first_trial):
        acts.insert(first_jog, {"step": TEST_STEP.format(loto=loto), "result": "ok"})
    return acts


def skeletons() -> list[dict]:
    P = Planner(SEED)
    return [P.build(i, p) for i, p in enumerate(P.plan())]


def case_id(sk: dict) -> str:
    return f"CASE-STP-{sk['idx'] + 1:04d}"


def sop_repair_summary(sop_id: str) -> str:
    p = KB / "sop" / LINE / f"{sop_id}.yaml"
    if not p.exists():
        return ""
    d = load_yaml(p)
    acts = [s["action"][:60] for s in d["steps"] if s["phase"] in ("inspect", "repair")]
    return "；".join(acts[:7])


def build_user(sk: dict, cat: dict) -> str:
    fm = cat[sk["fm_id"]]
    first = sk["first_hypothesis"]["failure_mode_id"]
    view = {k: sk[k] for k in ("case_type", "machine_id", "machine_type", "opened_at", "closed_at", "shift", "trigger",
                               "context", "log_signals", "first_hypothesis", "final_root_cause", "contributing_causes",
                               "hypothesis_correct", "outcome", "downtime_min", "parts_vocab", "technician",
                               "escalated_to", "new_mode", "false_alarm_story")}
    lines = ["## 骨架（事實，不可更改）", "```json", json.dumps(view, ensure_ascii=False, indent=1), "```",
             f"## 真正根因所屬模式：{sk['fm_id']}（{fm['label']}，category={fm['category']}）",
             f"criteria：{fm['criteria']}", f"常見症狀：{'、'.join(fm['symptoms'])}",
             f"對應 SOP {fm['sop']} 的檢查與修復重點：{sop_repair_summary(fm['sop'])}"]
    if first != sk["fm_id"] and first in cat:
        lines += [f"## 第一判斷的模式：{first}（{cat[first]['label']}）", f"criteria：{cat[first]['criteria']}"]
    for c in sk["contributing_causes"]:
        lines += [f"## 同時存在的次要原因：{c}（{cat[c]['label']}）", f"criteria：{cat[c]['criteria']}"]
    if sk["case_type"] == "false_alarm":
        lines += [f"## 這是誤報：{sk['false_alarm_story']}。final_root_cause=none，檢查後無故障。"]
    if sk["case_type"] == "new":
        lines += [f"## 這是目錄外的新故障模式：{sk['new_mode']['description']}。一開始被當成 {first}，"
                  f"照 SOP 處理無效，最後查出是上述新原因，移交 {sk['escalated_to']}，final_root_cause=new。"]
    if sk["case_type"] == "escalated":
        lines += [f"## 根因已確認是 {sk['fm_id']}，但現場無法完成修復，移交 {sk['escalated_to']}。"]
    if sk["case_type"] == "composite":
        lines += ["## 這是複合原因：兩個原因同時存在，actions 要兩個都處理到，normalized 要說明主因與次因。"]
    lines.append("請依 system 規則輸出 JSON。")
    return "\n".join(lines)


def draft(ids: set[int] | None, force: bool) -> None:
    from llm import MODEL, chat_json

    cat = catalog()
    system = (GEN / "prompts" / "case_gen_v1.system.md").read_text(encoding="utf-8")
    RAW.mkdir(parents=True, exist_ok=True)
    sks = [s for s in skeletons() if ids is None or s["idx"] + 1 in ids]

    def run(sk):
        cid = case_id(sk)
        p = RAW / f"{cid}.json"
        if p.exists() and not force:
            return cid, "cached"
        seed = 1000 + sk["idx"]
        try:
            out = chat_json(system, build_user(sk, cat), SCHEMA, temperature=0.8, seed=seed, retries=5)
        except Exception as e:  # noqa: BLE001
            return cid, f"FAILED {e}"
        p.write_text(json.dumps({"generator": {"model": MODEL, "prompt_ver": PROMPT_VER, "seed": seed},
                                 "output": out}, ensure_ascii=False, indent=1), encoding="utf-8")
        return cid, "ok"

    with cf.ThreadPoolExecutor(8) as ex:
        for cid, st in ex.map(run, sks):
            if st != "cached":
                print(cid, st, flush=True)


def bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / max(1, len(A | B))


def check(sk: dict, out: dict, cat: dict) -> list[str]:
    errs = []
    raw = out["technician_report_raw"]
    if not (10 <= len(raw) <= 140):
        errs.append(f"raw length {len(raw)}")
    norm = out["technician_report_normalized"]
    if not (50 <= len(norm) <= 320):
        errs.append(f"normalized length {len(norm)}")
    if re.search(r"[一-鿿][,;:]", norm):
        errs.append("half-width punctuation in normalized")
    if not set(out["parts_replaced"]) <= set(sk["parts_vocab"]):
        errs.append(f"parts outside vocab: {set(out['parts_replaced']) - set(sk['parts_vocab'])}")
    if sk["case_type"] == "false_alarm" and out["parts_replaced"]:
        errs.append("false alarm with parts")
    if not sk["hypothesis_correct"]:
        if not out.get("why_first_guess_wrong"):
            errs.append("missing why_first_guess_wrong")
        if len(out.get("discriminating_evidence") or []) < 2:
            errs.append("discriminating_evidence < 2")
    if not (2 <= len(out["actions"]) <= 7):
        errs.append(f"actions {len(out['actions'])}")
    text = json.dumps(out, ensure_ascii=False)
    bad = [w for w in SAFETY_BLACKLIST if w in text]
    if bad:
        errs.append(f"blacklist {bad}")
    if re.search(r"(先生|小姐|師傅[A-Za-z一-鿿]|[王李張陳林黃吳劉蔡楊][A-Za-z一-鿿]{1,2}(說|處理|回報))", text):
        errs.append("possible person name")
    return errs


def assemble() -> None:
    cat = catalog()
    sks = skeletons()
    OUT.mkdir(parents=True, exist_ok=True)
    HELD.mkdir(parents=True, exist_ok=True)
    # cloud-sync conflict copies ("CASE-STP-0001 2.json") are never valid cases
    for junk in [p for p in list(OUT.glob("CASE-STP-*.json")) + list(HELD.glob("CASE-STP-*.json")) if " " in p.name]:
        junk.unlink()
    patches = load_patches()
    review = (load_yaml(REVIEW) or {}) if REVIEW.exists() else {}
    approved = review.get("approved") or {}
    # held-out: exactly one case per failure mode, stratified over case types
    rnd = random.Random(SEED + 1)
    want = ["wrong_first"] * 5 + ["false_alarm"] * 2 + ["new", "escalated", "composite"] + ["typical"] * 10
    rnd.shuffle(want)
    held = set()
    by_mode = {}
    for s in sks:
        by_mode.setdefault(s["fm_id"], []).append(s)
    for i, fm_id in enumerate(cat):
        pool = [s for s in by_mode[fm_id] if s["case_type"] == want[i]] or [s for s in by_mode[fm_id] if s["case_type"] == "typical"]
        held.add(rnd.choice(pool)["idx"])

    problems, docs = {}, []
    for sk in sks:
        cid = case_id(sk)
        p = RAW / f"{cid}.json"
        if not p.exists():
            problems[cid] = ["missing draft"]
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        dh = draft_hash(raw["output"])
        patch = patches.get(cid) or {}
        pwarn: list = []
        out = apply_text_patch(cid, raw["output"], patch, pwarn)
        sk = json.loads(json.dumps(sk, ensure_ascii=False))
        drops = set(patch.get("drop_signals") or [])
        if drops:
            keep = [x for x in sk["log_signals"] if f"{x['t'][11:19]} {x['signal']}" not in drops]
            if len(sk["log_signals"]) - len(keep) != len(drops):
                pwarn.append("drop_signals not all matched")
            sk["log_signals"] = keep
        if patch.get("recent_changes"):
            sk["context"]["recent_changes"] = patch["recent_changes"]
        if patch.get("force_loto"):  # reviewer: hands entered the danger zone → LOTO is mandatory
            sk["safety_flags"] = {**sk["safety_flags"], "loto_done": True, "danger_zone_entry": True}
        for k, v in (patch.get("safety_flags") or {}).items():
            sk["safety_flags"][k] = v
        errs = check(sk, out, cat) + pwarn
        if errs:
            problems[cid] = errs
        actions = list(out["actions"])
        loto = LOTO_BY_TYPE[sk["machine_type"]]
        if sk["safety_flags"]["loto_done"]:
            li = int(patch.get("loto_at", 0))
            actions.insert(li, {"step": loto_step(sk, actions), "result": "ok"})
            actions = insert_energy_steps(actions, li, loto)
            if sk["outcome"] == "escalated":  # equipment handed over un-repaired: isolation stays on
                actions.append({"step": HANDOVER_STEP, "result": "保持隔離"})
        fm = cat[sk["fm_id"]]
        related = [x for x in dict.fromkeys([sk["final_root_cause"], *sk["contributing_causes"],
                                             sk["first_hypothesis"]["failure_mode_id"], sk["fm_id"]]) if x in cat]
        doc = {
            "case_id": cid, "data_label": "模擬資料", "synthetic": True,
            "generator": {**raw["generator"], "skeleton": f"case_skeleton.Planner(seed={SEED})"},
            "split": "heldout" if sk["idx"] in held else "train",
            "case_type": sk["case_type"], "line": LINE, "machine_type": sk["machine_type"], "machine_id": sk["machine_id"],
            "opened_at": sk["opened_at"], "closed_at": sk["closed_at"], "shift": sk["shift"],
            "trigger": sk["trigger"],
            "context": {"days_since_pm": sk["context"]["days_since_pm"], "recent_changes": sk["context"]["recent_changes"],
                        "material": sk["context"]["material"], "die_id": sk["context"]["die_id"], "part": sk["context"]["part"]},
            "log_signals": sk["log_signals"],
            "technician": sk["technician"],
            "technician_report_raw": out["technician_report_raw"].strip(),
            "technician_report_normalized": out["technician_report_normalized"].strip(),
            "first_hypothesis": sk["first_hypothesis"],
            "final_root_cause": sk["final_root_cause"],
            "contributing_causes": sk["contributing_causes"],
            "hypothesis_correct": sk["hypothesis_correct"],
            "why_first_guess_wrong": out["why_first_guess_wrong"] if not sk["hypothesis_correct"] else None,
            "discriminating_evidence": out["discriminating_evidence"] if not sk["hypothesis_correct"] else [],
            "new_mode_proposal": sk["new_mode"],
            "actions": actions,
            "sop_used": sk["sop_used"],
            "downtime_min": sk["downtime_min"],
            "parts_replaced": out["parts_replaced"],
            "safety_flags": sk["safety_flags"],
            "outcome": sk["outcome"],
            "escalated_to": sk["escalated_to"],
            "escalation_note": out.get("escalation_note") if sk["outcome"] == "escalated" else None,
            "lessons": out["lessons"].strip(),
            "related_failure_mode_ids": related,
            "category": fm["category"],
            "references": sk["sop_used"],
            "review_status": "draft",
        }
        ap = approved.get(cid)
        if ap:
            if ap == dh:
                doc.update({"review_status": "approved", "reviewed_by": list(review.get("reviewed_by") or []),
                            "review_note": review.get("review_note"), "review_date": review.get("review_date"),
                            "review_doc": review.get("review_doc")})
            else:
                problems.setdefault(cid, []).append("approval stale: draft text changed after review → left as draft")
        docs.append(doc)
    # near-duplicate check within the same failure mode
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if a["related_failure_mode_ids"][:1] == b["related_failure_mode_ids"][:1]:
                j = jaccard(a["technician_report_raw"], b["technician_report_raw"])
                if j > 0.6:
                    problems.setdefault(b["case_id"], []).append(f"near-duplicate of {a['case_id']} ({j:.2f})")
    keep_paths = set()
    for d in docs:
        target = HELD if d["split"] == "heldout" else OUT
        fp = target / f"{d['case_id']}.json"
        keep_paths.add(fp)
        txt = json.dumps(d, ensure_ascii=False, indent=2)
        if not fp.exists() or fp.read_text(encoding="utf-8") != txt:  # rewrite in place (no delete → no sync conflicts)
            fp.write_text(txt, encoding="utf-8")
    for old in list(OUT.glob("CASE-STP-*.json")) + list(HELD.glob("CASE-STP-*.json")):
        if old not in keep_paths:
            old.unlink()
    print(f"wrote {len(docs)} cases ({sum(d['split'] == 'heldout' for d in docs)} heldout)")
    for k, v in sorted(problems.items()):
        print("  CHECK", k, v)
    (GEN / "raw" / "case_check_report.json").write_text(json.dumps(problems, ensure_ascii=False, indent=1), encoding="utf-8")


def approve(ids: set[int] | None) -> None:
    """Pin the reviewer sign-off in case_review.yaml to the hash of each reviewed draft."""
    import yaml
    review = (load_yaml(REVIEW) or {}) if REVIEW.exists() else {}
    approved = review.get("approved") or {}
    for sk in skeletons():
        cid = case_id(sk)
        if ids is None or sk["idx"] + 1 in ids:
            approved[cid] = draft_hash(json.loads((RAW / f"{cid}.json").read_text(encoding="utf-8"))["output"])
    review["approved"] = dict(sorted(approved.items()))
    head = ("# 案例審核簽核（由 gen_cases.py approve 產生）。approved: case_id → 審核當時 LLM 草稿的 hash；\n"
            "# 草稿重新產生（hash 不符）時 assemble 會退回 draft，必須重新審核。\n")
    REVIEW.write_text(head + yaml.safe_dump(review, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"approved {len(approved)} cases → {REVIEW.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["draft", "assemble", "approve"])
    ap.add_argument("--ids")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(GEN))
    ids = {int(x) for x in a.ids.split(",")} if a.ids else None
    if a.cmd == "draft":
        draft(ids, a.force)
    elif a.cmd == "approve":
        approve(ids)
    else:
        assemble()
