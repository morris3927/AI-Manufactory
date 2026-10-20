"""Validate the knowledge base against the schemas in 07_RAG知識庫資料.md §4 and check
cross-references. Exit code 1 on any error.

Usage: python3.13 validate.py [-v]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from case_skeleton import PARTS, PM_CYCLE
from common import (KB, LINE, MACHINES, SAFETY_BLACKLIST, alarm_codes, catalog, load_yaml, loto_files,
                    regulation_chunks, signal_names, sources)

ERR: list[str] = []
WARN: list[str] = []
PHASES = ["prepare", "isolate", "inspect", "repair", "verify", "restore"]
MTYPES = {"servo_press", "mechanical_power_press", "servo_roll_feeder"}
MTYPES_ALL = MTYPES | {"robot_mig_cell", "spot_welder", "screwdriving_station", "manual_assembly_station"}  # welding / assembly (lines.py)
UNIT_RE = re.compile(r"\d+(\.\d+)?\s*(mm|bar|°C|%|t\b|A\b|N·m|ms|kN|V\b|MPa|HRB|°|次|件|模|分鐘|秒|小時|步|點)")
HALF_PUNCT = re.compile(r"[一-鿿][,;:](?!\d)")


HANDS_ON = re.compile(r"拆|手動檢查|手轉|清除模|清潔模面|開模|模內|定位銷|落料孔|銅棒|厚薄規|更換|側導板|鬆動")


def err(where, msg):
    ERR.append(f"{where}: {msg}")


def warn(where, msg):
    WARN.append(f"{where}: {msg}")


# ---------------------------------------------------------------- schemas (pydantic)
class Strict(BaseModel):
    model_config = ConfigDict(extra="allow")


class LogSignalSpec(Strict):
    signal: str
    pattern: Optional[str] = None
    op: Optional[str] = None
    value: object = None
    window_min: Optional[int] = None


class FailureMode(Strict):
    label: str
    criteria: str = Field(min_length=20)
    symptoms: list[str] = Field(min_length=2)
    log_signals: list[LogSignalSpec] = Field(min_length=1)
    distinguish_from: list[str]
    machine_types: list[str] = Field(min_length=1)
    category: Literal["equipment", "quality", "safety"]
    severity_default: Literal["S1", "S2", "S3"]
    loto_required: bool
    safety_only_sop: bool
    sop: str = Field(pattern=r"^SOP-STP-\d{3}$")
    sources: list[str] = Field(min_length=1)
    data_label: Literal["模擬資料"]
    review_status: Literal["draft", "reviewed", "approved"]
    reviewed_by: list[str] = []
    review_note: Optional[str] = None


class Step(Strict):
    no: int
    phase: Literal["prepare", "isolate", "inspect", "repair", "verify", "restore"]
    action: str = Field(min_length=6)
    criteria: Optional[str] = None
    caution: Optional[str] = None
    requires_supervisor: bool
    requires_energized: bool
    ref: list[str] = Field(min_length=1)


class SopSafety(Strict):
    loto_required: bool
    loto_checklist_id: str
    loto_checklist_ids: list[str]
    energy_sources: list[str]
    ppe: list[str]
    danger_zone_entry: bool
    legal_basis: list[str] = Field(min_length=1)


class Reference(Strict):
    title: str
    url: str
    license: str
    use: Literal["paraphrase", "quote", "reference"]


class Sop(Strict):
    sop_id: str = Field(pattern=r"^SOP-STP-\d{3}$")
    version: str
    title: str
    line: Literal["stamping"]
    machine_type: list[str]
    failure_mode_ids: list[str] = Field(min_length=1)
    data_label: Literal["模擬資料"]
    synthetic: Literal[True]
    author: str
    reviewed_by: list[str]
    review_status: Literal["draft", "reviewed", "approved"]
    review_note: Optional[str] = None
    safety: SopSafety
    symptoms: list[str] = Field(min_length=2)
    prerequisites: dict
    steps: list[Step] = Field(min_length=4)
    verify: list[str]
    restore: list[str] = Field(min_length=1)
    escalate_if: list[str] = Field(min_length=1)
    references: list[Reference] = Field(min_length=1)


class Energy(Strict):
    id: str
    type: str
    device: str
    method: str


class LotoStep(Strict):
    no: int
    action: str
    check: str


class Loto(Strict):
    loto_id: str
    review_status: Literal["draft", "reviewed", "approved"]
    reviewed_by: list[str] = []
    review_note: Optional[str] = None
    machine_type: str
    data_label: Literal["模擬資料"]
    legal_basis: list[str] = Field(min_length=3)
    permit: dict
    energy_sources: list[Energy] = Field(min_length=3)
    steps: list[LotoStep] = Field(min_length=5)
    release: list[str] = Field(min_length=3)
    ai_constraints: list[str] = Field(min_length=2)


class CaseSignal(Strict):
    t: str
    signal: str
    value: object
    machine_id: Optional[str] = None


class Hypothesis(Strict):
    failure_mode_id: str
    by: Literal["jev", "technician", "rule"]
    p: Optional[float] = None


class Action(Strict):
    step: str
    result: str


class Case(Strict):
    case_id: str = Field(pattern=r"^CASE-STP-\d{4}$")
    data_label: Literal["模擬資料"]
    synthetic: Literal[True]
    generator: dict
    split: Literal["train", "heldout"]
    case_type: Literal["typical", "wrong_first", "composite", "false_alarm", "escalated", "new"]
    line: Literal["stamping"]
    machine_type: str
    machine_id: str
    opened_at: dt.datetime
    closed_at: dt.datetime
    shift: Literal["day", "swing", "night"]
    trigger: dict
    context: dict
    log_signals: list[CaseSignal] = Field(min_length=1)
    technician_report_raw: str = Field(min_length=8)
    technician_report_normalized: str = Field(min_length=40)
    first_hypothesis: Hypothesis
    final_root_cause: str
    contributing_causes: list[str]
    hypothesis_correct: bool
    why_first_guess_wrong: Optional[str]
    discriminating_evidence: list[str]
    actions: list[Action] = Field(min_length=2)
    sop_used: list[str]
    downtime_min: int = Field(gt=0)
    parts_replaced: list[str]
    safety_flags: dict
    outcome: Literal["resolved", "false_alarm", "escalated"]
    lessons: str
    related_failure_mode_ids: list[str]
    references: list[str]
    review_status: Literal["draft", "reviewed", "approved"]


class RegChunk(Strict):
    chunk_id: str
    doc_id: str
    doc_type: Literal["regulation"]
    line: str
    source: str
    license: Literal["TW-law", "PD"]
    data_label: Literal["公開資料"]
    lang: Literal["zh-TW", "en"]
    review_status: Literal["approved"]
    article: str
    effective_date: str
    text: str = Field(min_length=30)


def model_check(model, data, where):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        for x in e.errors()[:6]:
            err(where, f"schema {'.'.join(str(i) for i in x['loc'])}: {x['msg']}")
        return None


FIELD_EXP = "現場經驗（模擬）"   # [ref:none] 步驟若標了這個字樣，代表審核者確認為模擬的現場經驗，不再列 warning


def check_review(where, raw):
    """review metadata: approved 需要 reviewed_by；AI 角色扮演審核必須附 review_note 且聲明非真人審核。"""
    st, by, note = raw.get("review_status"), raw.get("reviewed_by"), raw.get("review_note")
    if st not in ("draft", "reviewed", "approved"):
        err(where, f"review_status {st!r} invalid")
        return
    if by is not None and not isinstance(by, list):
        err(where, "reviewed_by must be a list")
    if note is not None and not isinstance(note, str):
        err(where, "review_note must be a string")
    if st == "approved" and not by:
        err(where, "approved requires reviewed_by")
    if any(str(x).startswith("ai_sim_") for x in (by or [])) and (not note or "非真人" not in note):
        err(where, "AI role-play reviewer requires review_note stating 非真人專家審核")


def bigram_jaccard(a, b):
    A = {a[i:i + 2] for i in range(len(a) - 1)}
    B = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(A & B) / max(1, len(A | B))


# ---------------------------------------------------------------- checks
def check_sources(src):
    for k, v in src.items():
        for f in ("title", "url", "license", "use"):
            if not v.get(f):
                err(f"SOURCES.{k}", f"missing {f}")
        if v["license"].startswith("©") and v["use"] == "ingest":
            err(f"SOURCES.{k}", "© source cannot be ingested")


def check_failure_modes(cat, src, sigs, alarms):
    if len(cat) != 20:
        err("failure_modes", f"expected 20 stamping modes, got {len(cat)}")
    for fid, raw in cat.items():
        w = f"failure_modes.{fid}"
        fm = model_check(FailureMode, raw, w)
        if not fm:
            continue
        if fm.label in fm.criteria:
            err(w, "criteria must not contain the label (Jev reads criteria literally)")
        for d in fm.distinguish_from:
            if d not in cat or d == fid:
                err(w, f"distinguish_from unknown {d}")
        for t in fm.machine_types:
            if t not in MTYPES:
                err(w, f"unknown machine_type {t}")
        for s in fm.sources:
            if s not in src:
                err(w, f"unknown source {s}")
        for ls in fm.log_signals:
            if ls.signal not in sigs:
                err(w, f"log signal {ls.signal} not in signals.yaml")
            if ls.signal == "alarm_code" and ls.value not in alarms:
                err(w, f"alarm code {ls.value} not in signals.yaml")
        if fm.category == "safety" and not fm.safety_only_sop:
            err(w, "safety category must have safety_only_sop: true")
        if UNIT_RE.search(fm.criteria) and "模擬值" not in fm.criteria:
            err(w, "numeric threshold in criteria without 模擬值")
        if HALF_PUNCT.search(fm.criteria):
            err(w, "half-width punctuation in criteria")
        if fm.category != "safety" and not fm.distinguish_from:
            warn(w, "no distinguish_from")
        check_review(w, raw)
    ids = list(cat)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            j = bigram_jaccard(cat[a]["criteria"], cat[b]["criteria"])
            if j > 0.45:
                err("failure_modes", f"criteria of {a} and {b} too similar ({j:.2f})")
    sops = Counter(v["sop"] for v in cat.values())
    for s, n in sops.items():
        if n > 1:
            err("failure_modes", f"SOP {s} shared by {n} modes")


def check_loto(lotos, regs):
    for lid, raw in lotos.items():
        w = f"loto/{lid}"
        l = model_check(Loto, raw, w)
        if not l:
            continue
        if l.loto_id != lid:
            err(w, "loto_id != filename")
        if l.machine_type not in MTYPES_ALL:
            err(w, f"unknown machine_type {l.machine_type}")
        for lb in l.legal_basis:
            for ref in re.findall(r"\[ref:([^\]]+)\]", lb):
                for r_ in [x.strip() for x in ref.split(",")]:
                    if r_ not in regs:
                        err(w, f"legal_basis ref {r_} not in public/regulations.jsonl")
        ids = [e.id for e in l.energy_sources]
        if len(ids) != len(set(ids)):
            err(w, "duplicate energy source ids")
        rel = "".join(l.release)
        if "第254條" not in rel or "不得代取" not in rel:
            err(w, "release must state original lock owner removes own lock (第254條, 不得代取)")
        if not raw.get("permit", {}).get("required"):
            err(w, "permit.required must be true (第57條 作業許可程序)")
        if not any("復電" in c for c in l.ai_constraints):
            err(w, "ai_constraints must forbid AI declaring re-energize")
        check_review(w, raw)
        if raw.get("review_status") != "approved":
            warn(w, f"review_status={raw.get('review_status')}")


def check_sops(cat, lotos, src, regs):
    sop_dir = KB / "sop" / LINE
    files = {p.stem: p for p in sop_dir.glob("SOP-*.yaml")}
    sop_ids = set(files)
    for fid, fm in cat.items():
        if fm["sop"] not in files:
            err(f"failure_modes.{fid}", f"SOP file {fm['sop']}.yaml missing")
    for sid, p in sorted(files.items()):
        w = f"sop/{sid}"
        raw = load_yaml(p)
        s = model_check(Sop, raw, w)
        if not s:
            continue
        if s.sop_id != sid:
            err(w, "sop_id != filename")
        for f in s.failure_mode_ids:
            if f not in cat:
                err(w, f"unknown failure_mode_id {f}")
            elif cat[f]["sop"] != sid:
                err(w, f"failure mode {f} points to {cat[f]['sop']}")
        fm = cat.get(s.failure_mode_ids[0], {})
        for t in s.machine_type:
            if t not in MTYPES:
                err(w, f"unknown machine_type {t}")
        for lid in [s.safety.loto_checklist_id, *s.safety.loto_checklist_ids]:
            if lid not in lotos:
                err(w, f"LOTO checklist {lid} does not exist")
        # step numbering and phase order
        nos = [x.no for x in s.steps]
        if nos != list(range(1, len(nos) + 1)):
            err(w, "step numbers not 1..n")
        order = [PHASES.index(x.phase) for x in s.steps]
        if order != sorted(order):
            err(w, "phases out of order (prepare→isolate→inspect→repair→verify→restore)")
        first_iso = next((i for i, x in enumerate(s.steps) if x.phase == "isolate"), None)
        first_work = next((i for i, x in enumerate(s.steps) if x.phase in ("inspect", "repair")), None)
        if s.safety.loto_required and (first_iso is None or (first_work is not None and first_iso > first_work)):
            err(w, "isolate step must come before inspect/repair")
        if first_iso is not None and s.safety.loto_checklist_id not in s.steps[first_iso].action:
            err(w, "isolate step does not reference the LOTO checklist")
        last = s.steps[-1] if s.steps[-1].phase == "restore" else None
        restore_txt = " ".join(x.action for x in s.steps if x.phase == "restore")
        if not last or "原上鎖人" not in restore_txt or "第254條" not in restore_txt:
            err(w, "restore must have original lock owner remove lock (第254條)")
        if "AI 不得宣告可以復電" not in restore_txt:
            err(w, "restore must state AI cannot declare re-energize")
        # energized steps must be preceded by testing/positioning procedure
        tp_seen = False
        for x in s.steps:
            if "測試／定位程序" in x.action:
                tp_seen = True
            if x.requires_energized and not tp_seen:
                err(w, f"step {x.no} energized before testing/positioning procedure")
        # die work → 第69條 safety block, die adjust → 第70條 inching
        all_txt = json.dumps(raw["steps"], ensure_ascii=False)
        if "設施規則第69條" in s.safety.legal_basis and "安全塊" not in all_txt:
            err(w, "第69條 listed but no safety block step")
        if "設施規則第70條" in s.safety.legal_basis and "寸動" not in all_txt:
            err(w, "第70條 listed but no inching step")
        # refs
        for x in s.steps:
            for r_ in x.ref:
                if r_ != "none" and r_ not in src and r_ not in regs:
                    err(w, f"step {x.no} ref {r_} unknown")
            if all(r_ == "none" for r_ in x.ref) and x.phase in ("inspect", "repair", "verify") \
                    and FIELD_EXP not in x.action:
                warn(w, f"step {x.no} has [ref:none] — reviewer must confirm (or mark {FIELD_EXP})")
            for fld in (x.action, x.criteria or "", x.caution or ""):
                if x.phase in ("inspect", "repair", "verify") and UNIT_RE.search(fld) and "模擬值" not in fld \
                        and "第" not in fld[:3]:
                    err(w, f"step {x.no} numeric value without 模擬值: {fld[:40]}")
                if HALF_PUNCT.search(fld):
                    err(w, f"step {x.no} half-width punctuation: {fld[:30]}")
        bad = [b for b in SAFETY_BLACKLIST if b in all_txt]
        if bad:
            err(w, f"blacklisted wording {bad}")
        for e in s.escalate_if:
            for ref in re.findall(r"SOP-STP-\d{3}", e):
                if ref not in sop_ids:
                    err(w, f"escalate_if references unknown {ref}")
                if ref == sid:
                    err(w, "escalate_if points to itself")
        for r_ in s.references:
            if r_.license.startswith("©") and r_.use != "paraphrase":
                err(w, "© reference must be use: paraphrase")
        if fm.get("safety_only_sop"):
            if any(x.phase == "repair" for x in s.steps):
                err(w, "safety-only SOP must not contain repair steps")
            if raw.get("generator", {}).get("model"):
                err(w, "safety-only SOP must not be LLM-generated")
        check_review(w, raw)
        if s.review_status != "approved":
            warn(w, f"review_status={s.review_status} (KB ingest gate rejects until approved)")


def check_regs(regs):
    p = KB / "public" / "regulations.jsonl"
    seen = set()
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        c = json.loads(line)
        w = f"public/regulations.jsonl:{i}"
        model_check(RegChunk, c, w)
        if c["chunk_id"] in seen:
            err(w, f"duplicate chunk_id {c['chunk_id']}")
        seen.add(c["chunk_id"])
        if not re.match(r"^https://(law\.moj\.gov\.tw|www\.ecfr\.gov)/", c["source"]):
            err(w, "source must be the official law.moj.gov.tw / ecfr.gov page")
        n = len(c["text"]) if c["lang"] == "zh-TW" else len(c["text"].split())
        if n > 520:
            warn(w, f"chunk long ({n})")
    for must in ["REG-N0060009-57-p1", "REG-N0060009-69", "REG-N0060009-70", "REG-N0060009-254-p1"]:
        if must not in regs:
            err("public/regulations.jsonl", f"missing required article chunk {must}")


def check_cases(cat, sop_ids, sigs, alarms):
    base = KB / "cases" / LINE
    def case_files(d):
        out = {}
        for p in d.glob("CASE-STP-*.json"):
            if re.fullmatch(r"CASE-STP-\d{4}", p.stem):
                out[p.stem] = p
            else:  # e.g. cloud-sync conflict copy "CASE-STP-0001 2.json" — not a case, never ingest
                err(f"cases/{p.name}", "stray file (sync-conflict duplicate like 'CASE-STP-0001 2.json'?) — delete it; never ingest")
        return out

    train = case_files(base)
    held = case_files(base / "heldout")
    overlap = set(train) & set(held)
    if overlap:
        err("cases", f"heldout overlaps train: {sorted(overlap)}")
    if len(held) != 20:
        err("cases", f"expected 20 heldout cases, got {len(held)}")
    docs = []
    for split, files in (("train", train), ("heldout", held)):
        for cid, p in sorted(files.items()):
            w = f"cases/{'heldout/' if split == 'heldout' else ''}{cid}"
            raw = json.loads(p.read_text(encoding="utf-8"))
            c = model_check(Case, raw, w)
            if not c:
                continue
            docs.append((c, raw))
            if c.case_id != cid:
                err(w, "case_id != filename")
            if c.split != split:
                err(w, f"split field {c.split} but stored in {split}")
            if c.machine_id not in MACHINES or MACHINES[c.machine_id]["type"] != c.machine_type:
                err(w, f"machine {c.machine_id}/{c.machine_type} inconsistent")
            ids = [c.first_hypothesis.failure_mode_id, *c.contributing_causes, *c.related_failure_mode_ids]
            for f in ids:
                if f not in cat:
                    err(w, f"unknown failure_mode_id {f}")
            if c.final_root_cause not in cat and c.final_root_cause not in ("new", "none"):
                err(w, f"unknown final_root_cause {c.final_root_cause}")
            if c.hypothesis_correct != (c.first_hypothesis.failure_mode_id == c.final_root_cause):
                err(w, "hypothesis_correct inconsistent with first_hypothesis/final_root_cause")
            if not c.hypothesis_correct and (not c.why_first_guess_wrong or len(c.discriminating_evidence) < 2):
                err(w, "wrong first hypothesis needs why_first_guess_wrong and ≥2 discriminating_evidence")
            if c.case_type == "wrong_first":
                fin = cat.get(c.final_root_cause, {})
                if c.first_hypothesis.failure_mode_id not in fin.get("distinguish_from", []):
                    err(w, "wrong first hypothesis must come from distinguish_from of the true mode")
            if c.case_type == "false_alarm" and (c.outcome != "false_alarm" or c.final_root_cause != "none"):
                err(w, "false_alarm case must have outcome false_alarm and final_root_cause none")
            if c.case_type == "new" and (c.final_root_cause != "new" or not raw.get("new_mode_proposal")):
                err(w, "new case needs final_root_cause=new and new_mode_proposal")
            if c.first_hypothesis.by == "jev" and c.first_hypothesis.p is None:
                err(w, "jev hypothesis needs p")
            for sid in [*c.sop_used, *c.references]:
                if sid not in sop_ids:
                    err(w, f"unknown SOP {sid}")
            if c.closed_at <= c.opened_at:
                err(w, "closed_at <= opened_at")
            span = (c.closed_at - c.opened_at).total_seconds() / 60
            if span + 0.5 < c.downtime_min or span > c.downtime_min + 10:
                err(w, f"downtime {c.downtime_min} inconsistent with timestamps ({span:.0f} min)")
            h = c.opened_at.hour
            want = "day" if 8 <= h < 16 else ("swing" if h >= 16 else "night")
            if c.shift != want:
                err(w, f"shift {c.shift} but opened at {h}h")
            for s in c.log_signals:
                if s.signal not in sigs:
                    err(w, f"signal {s.signal} not in signals.yaml")
                if s.signal == "alarm_code" and s.value not in alarms:
                    err(w, f"alarm code {s.value} unknown")
                if s.machine_id and s.machine_id not in MACHINES:
                    err(w, f"signal machine_id {s.machine_id} unknown")
                try:
                    dt.datetime.fromisoformat(s.t)
                except ValueError:
                    err(w, f"signal time {s.t} not ISO-8601")
            fmode = c.final_root_cause if c.final_root_cause in cat else c.first_hypothesis.failure_mode_id
            vocab = set(PARTS.get(c.final_root_cause, [])) | {p_ for cc in c.contributing_causes for p_ in PARTS.get(cc, [])}
            if not set(c.parts_replaced) <= vocab:
                err(w, f"parts outside vocabulary {set(c.parts_replaced) - vocab}")
            if HALF_PUNCT.search(c.technician_report_normalized):
                warn(w, "half-width punctuation in normalized report")
            txt = json.dumps(raw, ensure_ascii=False)
            bad = [b for b in SAFETY_BLACKLIST if b in txt]
            if bad:
                err(w, f"blacklisted wording {bad}")
            if cat.get(fmode, {}).get("loto_required") and c.safety_flags.get("danger_zone_entry") and not c.safety_flags.get("loto_done"):
                err(w, "danger zone entry without LOTO")
            # reviewer rule (2026-10-21): hands-on work in the die / feeder pinch area implies danger-zone entry
            if not c.safety_flags.get("loto_done"):
                hands = [a.step for a in c.actions if HANDS_ON.search(a.step)]
                if hands:
                    err(w, f"hands-on step without LOTO: {hands[0][:30]}")
            if c.safety_flags.get("loto_done") and not any("上鎖" in a.step for a in c.actions):
                err(w, "loto_done but no LOTO step in actions")
    # machine calendar consistency: no overlap, days_since_pm follows the PM cycle
    by_m = defaultdict(list)
    for c, raw in docs:
        by_m[c.machine_id].append(c)
    for m, cs in by_m.items():
        cs.sort(key=lambda x: x.opened_at)
        for a, b in zip(cs, cs[1:]):
            if b.opened_at < a.closed_at:
                err(f"cases/{b.case_id}", f"overlaps {a.case_id} on {m}")
            days = (b.opened_at.date() - a.opened_at.date()).days
            if (a.context["days_since_pm"] + days) % PM_CYCLE != b.context["days_since_pm"]:
                err(f"cases/{b.case_id}", f"days_since_pm inconsistent with {a.case_id} on {m}")
    # distribution + leakage
    n = len(docs)
    types = Counter(c.case_type for c, _ in docs)
    wrong = sum(1 for c, _ in docs if not c.hypothesis_correct and c.case_type in ("wrong_first", "composite"))
    if not (0.15 <= types["wrong_first"] / max(1, n) <= 0.25):
        err("cases", f"wrong_first share {types['wrong_first']}/{n} outside 15–25%")
    per_mode = Counter(c.related_failure_mode_ids[0] for c, _ in docs if c.related_failure_mode_ids)
    for f in cat:
        if per_mode[f] < 4:
            err("cases", f"mode {f} has only {per_mode[f]} cases")
    held_modes = Counter((c.final_root_cause if c.final_root_cause in cat else c.first_hypothesis.failure_mode_id)
                         for c, _ in docs if c.split == "heldout")
    tr = [c for c, _ in docs if c.split == "train"]
    for c, _ in docs:
        if c.split != "heldout":
            continue
        for t in tr:
            j = bigram_jaccard(c.technician_report_raw, t.technician_report_raw)
            if j > 0.6:
                err(f"cases/heldout/{c.case_id}", f"near-duplicate of train {t.case_id} ({j:.2f})")
    return {"total": n, "train": len(train), "heldout": len(held), "types": dict(types),
            "wrong_first_hypothesis_total": sum(1 for c, _ in docs if not c.hypothesis_correct),
            "heldout_modes": len(held_modes)}


def main():
    verbose = "-v" in sys.argv
    src = sources()
    cat = catalog()
    sigs, alarms = signal_names(), alarm_codes()
    lotos = loto_files()
    regs = regulation_chunks()
    check_sources(src)
    check_failure_modes(cat, src, sigs, alarms)
    check_loto(lotos, regs)
    check_regs(regs)
    check_sops(cat, lotos, src, regs)
    sop_ids = {p.stem for p in (KB / "sop" / LINE).glob("SOP-*.yaml")}
    stats = check_cases(cat, sop_ids, sigs, alarms)
    import validate_lines  # welding / assembly (same ERR/WARN lists via this module object)
    line_stats = validate_lines.run(sys.modules[__name__], src, sigs, alarms, lotos, regs)
    print("== Sentinel AI knowledge base validation ==")
    print(f"failure modes: {len(cat)} | SOPs: {len(sop_ids)} | LOTO: {len(lotos)} | regulation chunks: {len(regs)}")
    print(f"cases: {stats}")
    for ls in line_stats:
        print(f"{ls['line']}: {ls}")
    print(f"warnings: {len(WARN)}")
    if verbose:
        for w in WARN:
            print("  WARN", w)
    else:
        for k, v in Counter(w.split(": ", 1)[1].split(" (")[0][:60] for w in WARN).most_common(8):
            print(f"  {v:3d} × {k}")
    print(f"errors: {len(ERR)}")
    for e in ERR:
        print("  ERROR", e)
    print("RESULT:", "PASS" if not ERR else "FAIL")
    sys.exit(1 if ERR else 0)


if __name__ == "__main__":
    main()
