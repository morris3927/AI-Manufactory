"""Validation for the welding / assembly knowledge bases, called from validate.main().

Reuses validate.py's schemas, helpers and ERR/WARN lists (passed in as the running module `v`,
so that `python3.13 validate.py` reports one combined result). Stamping checks are untouched.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter, defaultdict
from typing import Literal, Optional

from pydantic import Field

from case_skeleton_wa import PARTS as PARTS_WA
from common import KB, SAFETY_BLACKLIST, catalog, guidance_chunks, load_yaml
from lines import LINES

EXTRA_BLACKLIST = ["釋放煞車", "放寬判定", "修改安全參數"]
HANDS_ON = {"welding": re.compile(r"拆|更換|剪|清潔噴嘴|圍欄內|電極之間|換cap"),
            "assembly": re.compile(r"拆|更換|清卡釘|送釘軌道|開蓋|換bit|換起子頭")}
GUIDE_SRC = re.compile(r"^https://(www\.osha\.gov|standards\.nasa\.gov|www\.hse\.gov\.uk)/")


def models(v):
    class FM(v.FailureMode):
        sop: str = Field(pattern=r"^SOP-(WLD|ASM)-\d{3}$")

    class Safety(v.SopSafety):
        loto_checklist_id: Optional[str] = None

    class Sop(v.Sop):
        sop_id: str = Field(pattern=r"^SOP-(WLD|ASM)-\d{3}$")
        line: Literal["welding", "assembly"]
        safety: Safety

    class Case(v.Case):
        case_id: str = Field(pattern=r"^CASE-(WLD|ASM)-\d{4}$")
        line: Literal["welding", "assembly"]

    class Guide(v.Strict):
        chunk_id: str
        doc_id: str
        doc_type: Literal["guideline", "standard"]
        line: str
        source: str
        license: Literal["PD", "OGL"]
        license_note: str = Field(min_length=20)
        data_label: Literal["公開資料"]
        lang: Literal["en", "zh-TW"]
        review_status: Literal["approved"]
        text: str = Field(min_length=30)

    return FM, Sop, Case, Guide


def check_public(v, regs):
    _, _, _, Guide = models(v)
    for p in sorted((KB / "public").glob("regulations_*.jsonl")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            c = json.loads(line)
            w = f"public/{p.name}:{i}"
            v.model_check(v.RegChunk, c, w)
            if not re.match(r"^https://(law\.moj\.gov\.tw|www\.ecfr\.gov)/", c["source"]):
                v.err(w, "source must be the official law.moj.gov.tw / ecfr.gov page")
    seen = set()
    for p in sorted((KB / "public").glob("guidance_*.jsonl")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            c = json.loads(line)
            w = f"public/{p.name}:{i}"
            v.model_check(Guide, c, w)
            if c["chunk_id"] in seen or c["chunk_id"] in regs:
                v.err(w, f"duplicate chunk_id {c['chunk_id']}")
            seen.add(c["chunk_id"])
            if not GUIDE_SRC.match(c["source"]):
                v.err(w, "guidance source must be an official osha.gov / standards.nasa.gov / hse.gov.uk URL")


def check_line(v, line, src, sigs, alarms, lotos, regs):
    FM, Sop, Case, _ = models(v)
    cfg = LINES[line]
    pre = cfg["prefix"]
    mtypes = set(cfg["machine_type_label"])
    cat = catalog(line)
    pubs = {**regs, **guidance_chunks()}
    # ---------------- failure modes
    if len(cat) != 10:
        v.err(f"failure_modes/{line}", f"expected 10 modes, got {len(cat)}")
    for fid, raw in cat.items():
        w = f"failure_modes/{line}.{fid}"
        fm = v.model_check(FM, raw, w)
        if not fm:
            continue
        if not fm.sop.startswith(f"SOP-{pre}-"):
            v.err(w, f"sop must be SOP-{pre}-xxx")
        if fm.label in fm.criteria:
            v.err(w, "criteria must not contain the label")
        for d in fm.distinguish_from:
            if d not in cat or d == fid:
                v.err(w, f"distinguish_from unknown {d}")
        for t in fm.machine_types:
            if t not in mtypes:
                v.err(w, f"unknown machine_type {t}")
        for s in fm.sources:
            if s not in src:
                v.err(w, f"unknown source {s}")
        for ls in fm.log_signals:
            if ls.signal not in sigs:
                v.err(w, f"log signal {ls.signal} not in signals.yaml")
            if ls.signal == "alarm_code" and ls.value not in alarms:
                v.err(w, f"alarm code {ls.value} not in signals.yaml")
        if fm.category == "safety" and not fm.safety_only_sop:
            v.err(w, "safety category must have safety_only_sop: true")
        if v.UNIT_RE.search(fm.criteria) and "模擬值" not in fm.criteria:
            v.err(w, "numeric threshold in criteria without 模擬值")
        if v.HALF_PUNCT.search(fm.criteria):
            v.err(w, "half-width punctuation in criteria")
        v.check_review(w, raw)
        if raw.get("review_status") != "approved":
            v.warn(w, f"review_status={raw.get('review_status')}")
    ids = list(cat)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            j = v.bigram_jaccard(cat[a]["criteria"], cat[b]["criteria"])
            if j > 0.45:
                v.err(f"failure_modes/{line}", f"criteria of {a} and {b} too similar ({j:.2f})")
    if len({x["sop"] for x in cat.values()}) != len(cat):
        v.err(f"failure_modes/{line}", "SOP shared by several modes")
    # ---------------- SOPs
    sop_dir = KB / "sop" / line
    files = {}
    for p in sop_dir.glob("*.yaml"):
        if re.fullmatch(rf"SOP-{pre}-\d{{3}}", p.stem):
            files[p.stem] = p
        else:
            v.err(f"sop/{line}/{p.name}", "stray file (sync-conflict duplicate?) — delete it; never ingest")
    for fid, fm in cat.items():
        if fm["sop"] not in files:
            v.err(f"failure_modes/{line}.{fid}", f"SOP file {fm['sop']}.yaml missing")
    for sid, p in sorted(files.items()):
        w = f"sop/{line}/{sid}"
        raw = load_yaml(p)
        s = v.model_check(Sop, raw, w)
        if not s:
            continue
        if s.sop_id != sid or s.line != line:
            v.err(w, "sop_id / line mismatch")
        fm = cat.get(s.failure_mode_ids[0], {})
        if fm.get("sop") != sid:
            v.err(w, "failure_mode_ids[0] does not point to this SOP")
        for t in s.machine_type:
            if t not in mtypes:
                v.err(w, f"unknown machine_type {t}")
        ids_ = ([s.safety.loto_checklist_id] if s.safety.loto_checklist_id else []) + s.safety.loto_checklist_ids
        if s.safety.loto_required and not ids_:
            v.err(w, "loto_required but no LOTO checklist")
        for lid in ids_:
            if lid not in lotos:
                v.err(w, f"LOTO checklist {lid} does not exist")
        nos = [x.no for x in s.steps]
        if nos != list(range(1, len(nos) + 1)):
            v.err(w, "step numbers not 1..n")
        order = [v.PHASES.index(x.phase) for x in s.steps]
        if order != sorted(order):
            v.err(w, "phases out of order")
        first_iso = next((i for i, x in enumerate(s.steps) if x.phase == "isolate"), None)
        first_work = next((i for i, x in enumerate(s.steps) if x.phase in ("inspect", "repair")), None)
        if s.safety.loto_required and (first_iso is None or (first_work is not None and first_iso > first_work)):
            v.err(w, "isolate step must come before inspect/repair")
        if first_iso is not None and s.safety.loto_checklist_id and s.safety.loto_checklist_id not in s.steps[first_iso].action:
            v.err(w, "isolate step does not reference the LOTO checklist")
        restore_txt = " ".join(x.action for x in s.steps if x.phase == "restore")
        if s.steps[-1].phase != "restore":
            v.err(w, "last step must be restore")
        if s.safety.loto_required:
            if "原上鎖人" not in restore_txt or "1910.147(e)(3)" not in restore_txt:
                v.err(w, "restore must have original lock owner remove lock (OSHA 1910.147(e)(3))")
            if re.search(r"原上鎖人[^；。]*（設施規則第254條）", restore_txt):
                v.err(w, "設施規則第254條 is electrical re-energizing confirmation, not lock removal by original locker")
            if "AI 不得宣告可以復電" not in restore_txt:
                v.err(w, "restore must state AI cannot declare re-energize")
        elif "AI 不得宣告可以恢復作業" not in restore_txt:
            v.err(w, "restore must state AI cannot declare resumption")
        tp = False
        for x in s.steps:
            if "測試／定位程序" in x.action:
                tp = True
            if x.requires_energized and not tp:
                v.err(w, f"step {x.no} energized before testing/positioning procedure")
        all_txt = json.dumps(raw["steps"], ensure_ascii=False)
        legal = " ".join(s.safety.legal_basis)
        if "第27條" in legal and "教導模式" not in all_txt:
            v.err(w, "robot standard 第27條 listed but no teach-mode step")
        if "1910.255(b)(8)" in legal and "安全插銷" not in all_txt:
            v.err(w, "1910.255(b)(8) listed but no safety-pin step")
        if "robot_mig_cell" in s.machine_type and s.safety.loto_required and not fm.get("safety_only_sop") and "禁止觸動" not in all_txt:
            v.err(w, "robot cell entry needs 禁止觸動 tag step (工業用機器人危害預防標準第33條)")
        if "第173條" in legal and "動火許可" not in all_txt:
            v.err(w, "第173條 listed but no hot-work permit step")
        for x in s.steps:
            for r_ in x.ref:
                if r_ != "none" and r_ not in src and r_ not in pubs:
                    v.err(w, f"step {x.no} ref {r_} unknown")
            if all(r_ == "none" for r_ in x.ref) and x.phase in ("inspect", "repair", "verify") and v.FIELD_EXP not in x.action:
                v.warn(w, f"step {x.no} has [ref:none] — reviewer must confirm")
            for fld in (x.action, x.criteria or "", x.caution or ""):
                if x.phase in ("inspect", "repair", "verify") and v.UNIT_RE.search(fld) and "模擬值" not in fld and "第" not in fld[:3]:
                    v.err(w, f"step {x.no} numeric value without 模擬值: {fld[:40]}")
                if v.HALF_PUNCT.search(fld):
                    v.err(w, f"step {x.no} half-width punctuation: {fld[:30]}")
        bad = [b for b in SAFETY_BLACKLIST + EXTRA_BLACKLIST if b in all_txt]
        if bad:
            v.err(w, f"blacklisted wording {bad}")
        for e in s.escalate_if:
            for ref in re.findall(r"SOP-[A-Z]{3}-\d{3}", e):
                if ref not in files:
                    v.err(w, f"escalate_if references unknown {ref}")
                if ref == sid:
                    v.err(w, "escalate_if points to itself")
        for r_ in s.references:
            if r_.license.startswith("©") and r_.use != "paraphrase":
                v.err(w, "© reference must be use: paraphrase")
        if fm.get("safety_only_sop"):
            if any(x.phase == "repair" for x in s.steps):
                v.err(w, "safety-only SOP must not contain repair steps")
            if raw.get("generator", {}).get("model"):
                v.err(w, "safety-only SOP must not be LLM-generated")
        v.check_review(w, raw)
        if s.review_status != "approved":
            v.warn(w, f"review_status={s.review_status}")
    # ---------------- cases
    base = KB / "cases" / line
    machines = cfg["machines"]
    parts = PARTS_WA[line]

    def case_files(d):
        out = {}
        for p in d.glob("*.json"):
            if re.fullmatch(rf"CASE-{pre}-\d{{4}}", p.stem):
                out[p.stem] = p
            else:
                v.err(f"cases/{line}/{p.name}", "stray file (sync-conflict duplicate?) — delete it; never ingest")
        return out

    train, held = case_files(base), case_files(base / "heldout")
    if set(train) & set(held):
        v.err(f"cases/{line}", "heldout overlaps train")
    if len(held) != 10:
        v.err(f"cases/{line}", f"expected 10 heldout cases, got {len(held)}")
    if len(train) != 50:
        v.err(f"cases/{line}", f"expected 50 train cases, got {len(train)}")
    docs = []
    for split, fs in (("train", train), ("heldout", held)):
        for cid, p in sorted(fs.items()):
            w = f"cases/{line}/{'heldout/' if split == 'heldout' else ''}{cid}"
            raw = json.loads(p.read_text(encoding="utf-8"))
            c = v.model_check(Case, raw, w)
            if not c:
                continue
            docs.append((c, raw))
            if c.case_id != cid or c.split != split or c.line != line:
                v.err(w, "case_id / split / line mismatch")
            if c.machine_id not in machines or machines[c.machine_id]["type"] != c.machine_type:
                v.err(w, f"machine {c.machine_id}/{c.machine_type} inconsistent")
            for f in [c.first_hypothesis.failure_mode_id, *c.contributing_causes, *c.related_failure_mode_ids]:
                if f not in cat:
                    v.err(w, f"unknown failure_mode_id {f}")
            if c.final_root_cause not in cat and c.final_root_cause not in ("new", "none"):
                v.err(w, f"unknown final_root_cause {c.final_root_cause}")
            if c.hypothesis_correct != (c.first_hypothesis.failure_mode_id == c.final_root_cause):
                v.err(w, "hypothesis_correct inconsistent")
            if not c.hypothesis_correct and (not c.why_first_guess_wrong or len(c.discriminating_evidence) < 2):
                v.err(w, "wrong first hypothesis needs why_first_guess_wrong and ≥2 discriminating_evidence")
            if c.case_type == "wrong_first" and c.first_hypothesis.failure_mode_id not in cat.get(c.final_root_cause, {}).get("distinguish_from", []):
                v.err(w, "wrong first hypothesis must come from distinguish_from of the true mode")
            if c.case_type == "false_alarm" and (c.outcome != "false_alarm" or c.final_root_cause != "none"):
                v.err(w, "false_alarm case must have outcome false_alarm and final_root_cause none")
            if c.case_type == "new" and (c.final_root_cause != "new" or not raw.get("new_mode_proposal")):
                v.err(w, "new case needs final_root_cause=new and new_mode_proposal")
            if c.first_hypothesis.by == "jev" and c.first_hypothesis.p is None:
                v.err(w, "jev hypothesis needs p")
            for sid in [*c.sop_used, *c.references]:
                if sid not in files:
                    v.err(w, f"unknown SOP {sid}")
            span = (c.closed_at - c.opened_at).total_seconds() / 60
            if c.closed_at <= c.opened_at or span + 0.5 < c.downtime_min or span > c.downtime_min + 10:
                v.err(w, f"downtime {c.downtime_min} inconsistent with timestamps ({span:.0f} min)")
            h = c.opened_at.hour
            if c.shift != ("day" if 8 <= h < 16 else ("swing" if h >= 16 else "night")):
                v.err(w, f"shift {c.shift} but opened at {h}h")
            for s in c.log_signals:
                if s.signal not in sigs:
                    v.err(w, f"signal {s.signal} not in signals.yaml")
                if s.signal == "alarm_code" and s.value not in alarms:
                    v.err(w, f"alarm code {s.value} unknown")
                try:
                    dt.datetime.fromisoformat(s.t)
                except ValueError:
                    v.err(w, f"signal time {s.t} not ISO-8601")
            vocab = set(parts.get(c.final_root_cause, [])) | {x for cc in c.contributing_causes for x in parts.get(cc, [])}
            if not set(c.parts_replaced) <= vocab:
                v.err(w, f"parts outside vocabulary {set(c.parts_replaced) - vocab}")
            txt = json.dumps(raw, ensure_ascii=False)
            bad = [b for b in SAFETY_BLACKLIST + EXTRA_BLACKLIST if b in txt]
            if bad:
                v.err(w, f"blacklisted wording {bad}")
            fmode = c.final_root_cause if c.final_root_cause in cat else c.first_hypothesis.failure_mode_id
            needs = cat.get(fmode, {}).get("loto_required")
            if needs and c.safety_flags.get("danger_zone_entry") and not c.safety_flags.get("loto_done"):
                v.err(w, "danger zone entry without LOTO")
            if needs and not c.safety_flags.get("loto_done"):
                hands = [a.step for a in c.actions if HANDS_ON[line].search(a.step)]
                if hands:
                    v.err(w, f"hands-on step without LOTO: {hands[0][:30]}")
            if c.safety_flags.get("loto_done") and not any(("上鎖" in a.step or "插頭" in a.step) for a in c.actions[:1]):
                v.err(w, "loto_done but first action is not the LOTO step")
            if c.safety_flags.get("loto_done"):
                acts = [a.step for a in c.actions]
                jog = next((i for i, a in enumerate(acts) if re.search(r"試銲|試鎖|空送絲|教導模式|功能測試|扭力抽測", a)
                            and "測試／定位程序" not in a and not a.startswith(("調閱", "查閱", "讀取", "比對", "核對"))), None)
                tp = next((i for i, a in enumerate(acts) if "測試／定位程序" in a or "恢復生產前確認" in a or "恢復量產前確認" in a), None)
                if jog is not None and (tp is None or tp > jog):
                    v.warn(w, "energized step before testing/positioning step")
            v.check_review(w, raw)
            if raw.get("review_status") != "approved":
                v.warn(w, f"review_status={raw.get('review_status')}")
    by_m = defaultdict(list)
    for c, _ in docs:
        by_m[c.machine_id].append(c)
    for m, cs in by_m.items():
        cs.sort(key=lambda x: x.opened_at)
        for a, b in zip(cs, cs[1:]):
            if b.opened_at < a.closed_at:
                v.err(f"cases/{line}/{b.case_id}", f"overlaps {a.case_id} on {m}")
            days = (b.opened_at.date() - a.opened_at.date()).days
            if (a.context["days_since_pm"] + days) % cfg["pm_cycle"] != b.context["days_since_pm"]:
                v.err(f"cases/{line}/{b.case_id}", f"days_since_pm inconsistent with {a.case_id} on {m}")
    n = len(docs)
    types = Counter(c.case_type for c, _ in docs)
    if not (0.15 <= types["wrong_first"] / max(1, n) <= 0.25):
        v.err(f"cases/{line}", f"wrong_first share {types['wrong_first']}/{n} outside 15–25%")
    per_mode = Counter(c.related_failure_mode_ids[0] for c, _ in docs if c.related_failure_mode_ids)
    for f in cat:
        if per_mode[f] < 4:
            v.err(f"cases/{line}", f"mode {f} has only {per_mode[f]} cases")
    tr = [c for c, _ in docs if c.split == "train"]
    for c, _ in docs:
        if c.split == "heldout":
            for t in tr:
                j = v.bigram_jaccard(c.technician_report_raw, t.technician_report_raw)
                if j > 0.6:
                    v.err(f"cases/{line}/heldout/{c.case_id}", f"near-duplicate of train {t.case_id} ({j:.2f})")
    return {"line": line, "modes": len(cat), "sops": len(files), "cases": n, "train": len(train), "heldout": len(held),
            "types": dict(types)}


def run(v, src, sigs, alarms, lotos, regs):
    check_public(v, regs)
    out = []
    for line in LINES:
        if (KB / "failure_modes" / f"{line}.yaml").exists():
            out.append(check_line(v, line, src, sigs, alarms, lotos, regs))
    return out
