"""E1 根因判斷（Jev J1–J3）、E2 規則 vs Jev vs LLM、E3 校準曲線。

用法（在 backend/ 底下）：
  python -m sentinel.eval.judge_eval run       # 打 API（已有的結果會沿用，不重打）
  python -m sentinel.eval.judge_eval analyze   # 只用 raw/ 的結果重算指標

原始回應存在 eval/results/raw/*.jsonl（每行一次呼叫），指標存在 eval/results/e1_*.json、e2_*.json、e3_*.json。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

from ..jev import judge
from ..jev import questions as Q
from ..jev.client import JevUnavailable
from ..jev.gate import decide
from ..kb import ingest as ing
from . import common as C

REPEATS = 3
PERM_SEED = 20261021
SYNTH_SEED = 7777
SYNTH_N = 80
LLM_REPEATS = 1

# DeepSeek（Ollama Cloud）不回傳單次費用（訂閱制）。成本估算用的單價是「假設值」，
# 參考 DeepSeek 官方 API 對 Flash 等級模型的公開價位量級，報告中明確標示為估算。
LLM_PRICE_IN_PER_M = 0.14
LLM_PRICE_OUT_PER_M = 0.28


# ── raw 快取 ──────────────────────────────────────────────────
class RawLog:
    def __init__(self, name: str):
        self.path = C.RAW / name
        self.rows: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    if not d.get("error"):
                        self.rows[d["key"]] = d

    def has(self, key: str) -> bool:
        return key in self.rows

    def add(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        if not row.get("error"):
            self.rows[row["key"]] = row


# ── 合成 state（E3 補點；明確標示為合成）──────────────────────
def synthetic_items(kb, n: int = SYNTH_N, seed: int = SYNTH_SEED) -> list[C.EvalItem]:
    """用知識庫的案例骨架產生器（_gen/case_skeleton.py，與產生案例同一套程式事實）另外抽 n 筆沖壓事件。

    只用骨架（log_signals／trigger／context＋已知根因），沒有 LLM 文字；根因由程式決定，所以正確答案已知。
    骨架與入庫案例出自同一分佈 → 這是「同分佈合成測試」，不等於真實工廠資料。
    """
    gen = C.KNOWLEDGE / "_gen"
    sys.path.insert(0, str(gen))
    try:
        import case_skeleton as cs           # noqa: WPS433（只讀匯入，不修改知識庫）
    finally:
        sys.path.remove(str(gen))
    planner = cs.Planner(seed=seed)
    plans = planner.plan()
    rnd = random.Random(seed)
    rnd.shuffle(plans)
    out = []
    for i, p in enumerate(plans):
        if len(out) >= n:
            break
        if p["case_type"] in ("new",):
            continue
        try:
            sk = planner.build(1000 + i, p)
        except RuntimeError:
            continue
        case = {"case_id": f"SYN-{seed}-{i:03d}", "line": "stamping", "machine_type": sk["machine_type"],
                "machine_id": sk["machine_id"], "trigger": sk["trigger"], "context": sk["context"],
                "log_signals": sk["log_signals"], "final_root_cause": sk["final_root_cause"],
                "outcome": sk["outcome"], "category": kb.failure_modes["stamping"][sk["fm_id"]].get("category"),
                "first_hypothesis": sk["first_hypothesis"], "case_type": sk["case_type"]}
        inc, m, tl = C.case_to_incident(case)
        out.append(C.EvalItem(case["case_id"], "stamping", case, inc, m, tl, C.expected(case, kb), synthetic=True,
                              meta={"case_type": sk["case_type"]}))
    return out


# ── Jev ───────────────────────────────────────────────────────
def shuffled_modes(fms: dict[str, dict], seed: int, item_id: str) -> dict[str, dict]:
    keys = list(fms)
    random.Random(f"{seed}:{item_id}").shuffle(keys)
    if len(keys) > 1 and keys == list(fms):
        keys = keys[1:] + keys[:1]
    return {k: fms[k] for k in keys}


async def run_jev(items: list[C.EvalItem], log: RawLog, *, variant: str, repeats: int, conc: int = 4) -> None:
    kb = C.eval_kb()
    jev = C.jev_client()
    await jev.warmup()                         # 與正式環境相同：啟動時先暖機一次，不計入
    sem = asyncio.Semaphore(conc)

    async def one(it: C.EvalItem, rep: int) -> None:
        key = f"{variant}:{it.item_id}:{rep}"
        if log.has(key):
            return
        fms = kb.failure_modes_for(it.line, it.machine.type)
        order = list(fms)
        if variant == "perm":
            fms = shuffled_modes(fms, PERM_SEED, it.item_id)
        cases = kb.similar_cases(it.incident, k=5, machine_type=it.machine.type)
        async with sem:
            row: dict[str, Any] = {"key": key, "variant": variant, "item_id": it.item_id, "rep": rep,
                                   "synthetic": it.synthetic, "ts": time.time(),
                                   "options_order": list(fms), "default_order": order,
                                   "similar_cases": [c.case_id for c in cases],
                                   "similar_fm": [c.failure_mode_id for c in cases]}
            try:
                j = await judge.event(jev, it.incident, machine=it.machine, timeline=it.timeline, similar_cases=cases,
                                      failure_modes=fms,
                                      severity_notes=kb.severity_notes(it.line, it.machine.type))
                row["judgement"] = j.model_dump()
                row["decision"] = decide(it.incident.rule_severity, j,
                                         p_false_alarm=C.SETTINGS.thresholds.p_false_alarm,
                                         root_cause_conf=C.SETTINGS.thresholds.root_cause_conf).model_dump()
            except JevUnavailable as e:
                row["error"] = str(e)
            log.add(row)

    await asyncio.gather(*(one(it, r) for it in items for r in range(repeats)))
    await jev.aclose()


# ── DeepSeek（同一份 state、同一組題目，要求輸出每個選項的機率）────────
LLM_SYSTEM = (
    "你是工廠異常事件的判斷器。只依據使用者提供的 state（事件、10 分鐘時間線、機台脈絡、相似歷史案例）回答題目。"
    "每一題都要輸出機率：choice 題輸出每個選項的機率（加總為 1）；noul 題輸出「是」的機率（0～1）；"
    "score 題輸出每一級的機率（加總為 1）。只輸出 JSON，不要任何說明文字。"
)


def llm_messages(state: dict, questions: dict) -> list[dict]:
    q = questions
    schema = {
        "category": {k: "機率" for k in q["category"]["criteria"]},
        "is_false_alarm": "是誤報的機率（0～1）",
        "severity": {"S1": "機率", "S2": "機率", "S3": "機率"},
    }
    if "root_cause" in q:
        schema["root_cause"] = {k: "機率" for k in q["root_cause"]["criteria"]}
    user = ("## state\n" + json.dumps(state, ensure_ascii=False) + "\n\n## 題目\n"
            + json.dumps(q, ensure_ascii=False) + "\n\n## 輸出格式（JSON，值換成數字）\n"
            + json.dumps(schema, ensure_ascii=False))
    return [{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": user}]


def _norm(d: Any, keys: list[str]) -> dict[str, float]:
    if not isinstance(d, dict):
        return {k: 1 / len(keys) for k in keys}
    vals = {}
    for k in keys:
        v = d.get(k)
        try:
            vals[k] = max(0.0, float(v))
        except (TypeError, ValueError):
            vals[k] = 0.0
    s = sum(vals.values())
    return {k: (v / s if s else 1 / len(keys)) for k, v in vals.items()}


def parse_llm(content: str, questions: dict) -> dict:
    m = re.search(r"\{.*\}", content, re.S)
    d = json.loads(m.group(0) if m else content)
    out: dict[str, Any] = {}
    cat = _norm(d.get("category"), list(questions["category"]["criteria"]))
    out["category"] = max(cat, key=cat.get)
    out["category_probs"] = cat
    try:
        out["p_false_alarm"] = min(1.0, max(0.0, float(d.get("is_false_alarm"))))
    except (TypeError, ValueError):
        out["p_false_alarm"] = 0.5
    sev = _norm(d.get("severity"), ["S1", "S2", "S3"])
    out["severity_probs"] = sev
    out["jev_severity"] = max(sev, key=sev.get)
    if "root_cause" in questions:
        rc = _norm(d.get("root_cause"), list(questions["root_cause"]["criteria"]))
        top = sorted(rc.items(), key=lambda kv: -kv[1])
        out["root_cause_top3"] = [{"id": k, "p": round(p, 4)} for k, p in top[:3]]
        out["root_cause_conf"] = top[0][1]
    return out


async def run_llm(items: list[C.EvalItem], log: RawLog, *, repeats: int = LLM_REPEATS, conc: int = 4) -> None:
    kb = C.eval_kb()
    s = C.SETTINGS
    key_ = C.ollama_key()
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(timeout=120, headers={"Authorization": f"Bearer {key_}"}) as http:
        async def one(it: C.EvalItem, rep: int) -> None:
            key = f"llm:{it.item_id}:{rep}"
            if log.has(key):
                return
            fms = kb.failure_modes_for(it.line, it.machine.type)
            criteria = {fid: fm.get("criteria") or fm.get("label") or fid for fid, fm in fms.items()}
            questions = Q.event_questions(criteria, kb.severity_notes(it.line, it.machine.type))
            cases = kb.similar_cases(it.incident, k=5, machine_type=it.machine.type)
            state = judge.build_state(it.incident, it.machine, it.timeline, cases)
            body = {"model": s.llm.model, "messages": llm_messages(state, questions), "stream": False,
                    "format": "json", "think": False, "options": {"temperature": 0}}
            row: dict[str, Any] = {"key": key, "variant": "llm", "item_id": it.item_id, "rep": rep,
                                   "synthetic": it.synthetic, "model": s.llm.model}
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await http.post(s.llm.url, json=body)
                    r.raise_for_status()
                    d = r.json()
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    row["tokens_in"] = d.get("prompt_eval_count")
                    row["tokens_out"] = d.get("eval_count")
                    row["content"] = (d.get("message") or {}).get("content", "")
                    row["judgement"] = parse_llm(row["content"], questions)
                except Exception as e:          # 解析失敗也記錄（算成答錯，不重打）
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    row["error"] = f"{type(e).__name__}: {e}"[:300]
                log.add(row)

        await asyncio.gather(*(one(it, r) for it in items for r in range(repeats)))


# ── 純規則基準 ────────────────────────────────────────────────
def rules_baseline(items: list[C.EvalItem]) -> dict[str, dict]:
    """每個 (line, 觸發事件類型) 取訓練案例（非 heldout）中最常見的根因；類別同理；誤報一律 False；嚴重度 = 規則判定。"""
    train = ing.load_cases(C.KNOWLEDGE, heldout=False)
    by_evt: dict[tuple, Counter] = defaultdict(Counter)
    by_line: dict[str, Counter] = defaultdict(Counter)
    cat_evt: dict[tuple, Counter] = defaultdict(Counter)
    for c in train:
        rc = c.get("final_root_cause")
        evt = (c["line"], (c.get("trigger") or {}).get("event_type"))
        cat_evt[evt][c.get("category")] += 1
        if rc in (None, "none", "new"):
            continue
        by_evt[evt][rc] += 1
        by_line[c["line"]][rc] += 1
    out = {}
    for it in items:
        evt = (it.line, it.incident.type)
        cnt = by_evt.get(evt) or by_line[it.line]
        ranked = [k for k, _ in cnt.most_common()]
        for k, _ in by_line[it.line].most_common():          # 不足 3 個時用整條線的頻率補
            if k not in ranked:
                ranked.append(k)
        tot = sum(cnt.values())
        cats = cat_evt.get(evt) or Counter({"equipment": 1})
        out[it.item_id] = {"root_cause_top3": [{"id": k, "p": round(cnt.get(k, 0) / tot, 4) if tot else 0}
                                               for k in ranked[:3]],
                           "category": cats.most_common(1)[0][0], "p_false_alarm": 0.0,
                           "jev_severity": it.incident.rule_severity or "S2",
                           "matched": "event_type" if by_evt.get(evt) else "line"}
    return out


# ── 指標 ──────────────────────────────────────────────────────
def score_rows(items: dict[str, C.EvalItem], rows: list[dict], *, fa_thr: float = 0.5) -> dict[str, Any]:
    rc_n = rc1 = rc3 = 0
    cat_n = cat_ok = cat_ok_lenient = 0
    sev_n = sev_ok = sev_gate_ok = 0
    s3_n = s3_hit = 0
    tp = fp = fn = tn = 0
    gate_tp = gate_fp = 0
    lat, cost = [], []
    per_line: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    per_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    sev_conf: Counter = Counter()
    for r in rows:
        it = items[r["item_id"]]
        gt = it.gt
        j = r.get("judgement")
        if j is None:                                 # 錯誤／逾時：每個指標都算錯
            j = {"category": None, "p_false_alarm": 0.0, "jev_severity": None, "root_cause_top3": []}
        if r.get("latency_ms") is not None or j.get("latency_ms") is not None:
            lat.append(r.get("latency_ms") or j.get("latency_ms"))
        if j.get("cost") is not None:
            cost.append(j["cost"])
        top = [x["id"] for x in j.get("root_cause_top3") or []]
        if gt["rc_in_catalog"]:
            rc_n += 1
            h1, h3 = bool(top) and top[0] == gt["root_cause"], gt["root_cause"] in top[:3]
            rc1 += h1
            rc3 += h3
            pl = per_line[it.line]
            pl[0] += 1
            pl[1] += h1
            pl[2] += h3
            ct = it.meta.get("case_type") or "-"
            per_type[ct][0] += 1
            per_type[ct][1] += h1
        cat_n += 1
        cat_ok += j.get("category") == gt["category"]
        cat_ok_lenient += j.get("category") in {gt["category"], gt["case_category"]}
        sev_n += 1
        sev_ok += j.get("jev_severity") == gt["severity"]
        gated = max(it.incident.rule_severity or "S1", j.get("jev_severity") or "S1", key=C.SEV_RANK.get)
        sev_gate_ok += gated == gt["severity"]
        sev_conf[(gt["severity"], j.get("jev_severity"))] += 1
        if gt["severity"] == "S3":
            s3_n += 1
            s3_hit += gated == "S3"
        pred_fa = (j.get("p_false_alarm") or 0) > fa_thr
        if pred_fa and gt["is_false_alarm"]:
            tp += 1
        elif pred_fa:
            fp += 1
        elif gt["is_false_alarm"]:
            fn += 1
        else:
            tn += 1
        dec = r.get("decision") or {}
        if dec.get("action") == "log_only":
            gate_tp += gt["is_false_alarm"]
            gate_fp += not gt["is_false_alarm"]
    n_fa = tp + fn
    return {
        "n_calls": len(rows), "n_errors": sum(1 for r in rows if r.get("judgement") is None),
        "root_cause_top1": C.rate(rc1, rc_n), "root_cause_top3": C.rate(rc3, rc_n),
        "root_cause_by_line": {k: {"n": v[0], "top1": round(v[1] / v[0], 3), "top3": round(v[2] / v[0], 3)}
                               for k, v in sorted(per_line.items())},
        "root_cause_top1_by_case_type": {k: {"n": v[0], "top1": round(v[1] / v[0], 3)} for k, v in sorted(per_type.items())},
        "category": C.rate(cat_ok, cat_n), "category_lenient": C.rate(cat_ok_lenient, cat_n),
        "severity_raw": C.rate(sev_ok, sev_n), "severity_gated": C.rate(sev_gate_ok, sev_n),
        "severity_confusion": {f"{a}->{b}": v for (a, b), v in sorted(sev_conf.items(), key=str)},
        "s3_recall_gated": C.rate(s3_hit, s3_n),
        "false_alarm": {"threshold": fa_thr, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n_false_alarm_cases": n_fa,
                        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                        "recall": round(tp / n_fa, 3) if n_fa else None},
        "false_alarm_gate": {"rule": f"p_false_alarm > {C.SETTINGS.thresholds.p_false_alarm} 且非 S3 → log_only",
                             "suppressed_true_fa": gate_tp, "suppressed_real_events": gate_fp},
        "latency_ms": C.lat_stats([x for x in lat if x is not None]),
        "cost_usd": {"total": round(sum(cost), 6), "per_call": round(statistics.fmean(cost), 8) if cost else None},
    }


def stability(rows: list[dict]) -> dict[str, Any]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("judgement"):
            by[r["item_id"]].append(r["judgement"])
    same_top1 = same_cat = 0
    sds = []
    n = 0
    for js in by.values():
        if len(js) < 2:
            continue
        n += 1
        tops = [(j["root_cause_top3"] or [{"id": None}])[0]["id"] for j in js]
        same_top1 += len(set(tops)) == 1
        same_cat += len({j["category"] for j in js}) == 1
        ps = [(j["root_cause_top3"] or [{"p": 0}])[0]["p"] for j in js]
        sds.append(statistics.pstdev(ps))
    return {"n_items": n, "top1_identical_all_runs": C.rate(same_top1, n),
            "category_identical_all_runs": C.rate(same_cat, n),
            "top1_prob_sd_mean": round(statistics.fmean(sds), 4) if sds else None,
            "top1_prob_sd_max": round(max(sds), 4) if sds else None}


def position_bias(rows: list[dict]) -> dict[str, Any]:
    """Jev 選的 top-1 落在選項清單第幾個位置（Jev 已知偏好前面的選項）。"""
    pos = Counter()
    n = 0
    for r in rows:
        j = r.get("judgement")
        if not j or not j.get("root_cause_top3"):
            continue
        order = r.get("options_order") or []
        top = j["root_cause_top3"][0]["id"]
        if top in order:
            pos[order.index(top)] += 1
            n += 1
    return {"n": n, "top1_in_first_option": C.rate(pos.get(0, 0), n),
            "top1_in_first_3_options": C.rate(sum(pos.get(i, 0) for i in range(3)), n),
            "histogram": dict(sorted(pos.items()))}


def auroc(items: dict[str, C.EvalItem], rows: list[dict]) -> dict[str, Any]:
    pos = [r["judgement"]["p_false_alarm"] for r in rows if r.get("judgement") and items[r["item_id"]].gt["is_false_alarm"]]
    neg = [r["judgement"]["p_false_alarm"] for r in rows if r.get("judgement") and not items[r["item_id"]].gt["is_false_alarm"]]
    if not pos or not neg:
        return {"n_pos": len(pos), "n_neg": len(neg), "auroc": None}
    s = sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg)
    return {"n_pos": len(pos), "n_neg": len(neg), "auroc": round(s / (len(pos) * len(neg)), 3)}


def calibration_points(items: dict[str, C.EvalItem], rows: list[dict], field: str = "top1") -> list[tuple[float, int]]:
    pts = []
    for r in rows:
        j = r.get("judgement")
        it = items[r["item_id"]]
        if not j or not j.get("root_cause_top3") or not it.gt["rc_in_catalog"]:
            continue
        p = j["root_cause_top3"][0]["p"] if field == "top1" else j.get("root_cause_conf", 0)
        pts.append((float(p), int(j["root_cause_top3"][0]["id"] == it.gt["root_cause"])))
    return pts


def calib_bins(pts: list[tuple[float, int]], bins: int = 5) -> dict[str, Any]:
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        g = [(p, y) for p, y in pts if lo <= p < hi or (i == bins - 1 and p >= 1.0)]
        k = sum(y for _, y in g)
        out.append({"bin": [lo, hi], "n": len(g), "p_pred": round(statistics.fmean(p for p, _ in g), 4) if g else None,
                    "p_obs": round(k / len(g), 4) if g else None, "ci95": C.wilson(k, len(g))})
    n = len(pts)
    brier = round(sum((p - y) ** 2 for p, y in pts) / n, 4) if n else None
    ece = round(sum(b["n"] / n * abs(b["p_pred"] - b["p_obs"]) for b in out if b["n"]), 4) if n else None
    return {"n": n, "bins": out, "brier": brier, "ece": ece, "accuracy": round(sum(y for _, y in pts) / n, 4) if n else None,
            "mean_conf": round(statistics.fmean(p for p, _ in pts), 4) if n else None}


def analyze() -> dict[str, Any]:
    kb = C.eval_kb()
    held = C.heldout_items(kb)
    syn = synthetic_items(kb)
    items = {it.item_id: it for it in held + syn}
    tag = C.embedder_tag()
    jev_log, llm_log = RawLog(f"e1_jev{tag}.jsonl"), RawLog(f"e2_llm{tag}.jsonl")
    held_ids = {it.item_id for it in held}

    def rows(log: RawLog, variant: str, ids: set[str]) -> list[dict]:
        return [r for r in log.rows.values() if r["variant"] == variant and r["item_id"] in ids]

    base = rows(jev_log, "base", held_ids)
    perm = rows(jev_log, "perm", held_ids)
    rep0 = [r for r in base if r["rep"] == 0]
    for r in base + perm:
        r["latency_ms"] = (r.get("judgement") or {}).get("latency_ms")

    # E1
    e1 = {
        "description": "Jev 1.14 J1–J3（正式題目 " + Q.EVENT_VER + "）於保留測試集；每筆 3 次",
        "kb": C.kb_info(), "n_items": len(held),
        "items_by_line": dict(Counter(it.line for it in held)),
        "root_cause_scored_items": sum(1 for it in held if it.gt["rc_in_catalog"]),
        "all_runs": score_rows(items, base),
        "first_run_only": score_rows(items, rep0),
        "gate_threshold_fa": score_rows(items, base, fa_thr=C.SETTINGS.thresholds.p_false_alarm)["false_alarm"],
        "stability": stability(base),
        "position_bias_default_order": position_bias(base),
        "permutation": {"seed": PERM_SEED, "runs": 1, "scores": score_rows(items, perm),
                        "position_bias": position_bias(perm)},
        "diagnosis_mode": dict(Counter((r.get("decision") or {}).get("diagnosis_mode") for r in base)),
        "excluded_from_root_cause": [{"item": it.item_id, "gt": it.gt["root_cause"],
                                      "reason": "誤報（none）" if it.gt["root_cause"] == "none" else "目錄外新模式（new；題目 .4 起有「以上皆非」選項，另列 new_mode_case）"}
                                     for it in held if not it.gt["rc_in_catalog"]],
        "per_item": [{"item": it.item_id, "line": it.line, "case_type": it.meta.get("case_type"), "gt": it.gt,
                      "runs": [{"top3": (r.get("judgement") or {}).get("root_cause_top3"),
                                "category": (r.get("judgement") or {}).get("category"),
                                "p_false_alarm": (r.get("judgement") or {}).get("p_false_alarm"),
                                "severity": (r.get("judgement") or {}).get("jev_severity"),
                                "latency_ms": (r.get("judgement") or {}).get("latency_ms"),
                                "similar_fm": r.get("similar_fm")}
                               for r in sorted(base, key=lambda r: r["rep"]) if r["item_id"] == it.item_id]}
                     for it in held],
    }
    # 檢索本身的上限：相似案例裡有沒有正確根因
    hit = sum(1 for r in rep0 if items[r["item_id"]].gt["rc_in_catalog"]
              and items[r["item_id"]].gt["root_cause"] in (r.get("similar_fm") or []))
    nrc = sum(1 for r in rep0 if items[r["item_id"]].gt["rc_in_catalog"])
    e1["retrieval_gt_in_top5_cases"] = C.rate(hit, nrc)
    # 檢索多數決（不經 Jev）：相似案例最常見的根因
    maj = 0
    for r in rep0:
        it = items[r["item_id"]]
        if it.gt["rc_in_catalog"]:
            fm = [x for x in (r.get("similar_fm") or []) if x not in (None, "none", "new")]
            maj += bool(fm) and Counter(fm).most_common(1)[0][0] == it.gt["root_cause"]
    e1["retrieval_majority_top1"] = C.rate(maj, nrc)
    # 隨機猜的基準：正式流程依機型篩故障模式，候選數 3～16 個
    nopt = [len(r.get("options_order") or []) for r in rep0 if items[r["item_id"]].gt["rc_in_catalog"]]
    e1["options_per_item"] = {"min": min(nopt), "max": max(nopt), "mean": round(statistics.fmean(nopt), 2),
                              "hist": dict(sorted(Counter(nopt).items()))}
    e1["chance_baseline"] = {"top1": round(statistics.fmean(1 / n for n in nopt), 3),
                             "top3": round(statistics.fmean(min(3, n) / n for n in nopt), 3)}
    # 選項順序打亂前後 top-1 是否相同
    pm = {r["item_id"]: r for r in perm}
    same = sum(1 for r in rep0 if r["item_id"] in pm and (r.get("judgement") or {}).get("root_cause_top3")
               and (pm[r["item_id"]].get("judgement") or {}).get("root_cause_top3")
               and r["judgement"]["root_cause_top3"][0]["id"] == pm[r["item_id"]]["judgement"]["root_cause_top3"][0]["id"])
    e1["permutation"]["top1_same_as_default_order"] = C.rate(same, len(rep0))
    e1["permutation"]["top1_acc_change_pp"] = round(100 * ((e1["permutation"]["scores"]["root_cause_top1"]["rate"] or 0)
                                                           - (e1["first_run_only"]["root_cause_top1"]["rate"] or 0)), 1)
    # 誤報：p_false_alarm 的排序能力（AUROC），保留集＋合成集
    syn_rows_ = rows(jev_log, "synthetic", {it.item_id for it in syn})
    e1["false_alarm_auroc"] = {"heldout_first_run": auroc(items, rep0), "heldout_plus_synthetic": auroc(items, rep0 + syn_rows_),
                               "p_false_alarm_on_true_fa": sorted(round(r["judgement"]["p_false_alarm"], 3)
                                                                  for r in rep0 + syn_rows_ if r.get("judgement")
                                                                  and items[r["item_id"]].gt["is_false_alarm"]),
                               "p_false_alarm_max_on_real_events": max((r["judgement"]["p_false_alarm"] for r in rep0 + syn_rows_
                                                                        if r.get("judgement") and not items[r["item_id"]].gt["is_false_alarm"]), default=None)}
    e1["category_confusion"] = {f"{a}->{b}": v for (a, b), v in sorted(Counter(
        (items[r["item_id"]].gt["category"], (r.get("judgement") or {}).get("category")) for r in rep0).items(), key=str)}
    e1["new_mode_case"] = [{"item": r["item_id"], "jev_top1": r["judgement"]["root_cause_top3"][0]}
                           for r in rep0 if items[r["item_id"]].gt["root_cause"] == "new" and r.get("judgement")]
    C.save_json(f"e1_root_cause_jev{C.embedder_tag()}.json", e1)

    # E2
    rb = rules_baseline(held)
    rule_rows = [{"item_id": k, "judgement": v, "latency_ms": None} for k, v in rb.items()]
    llm_rows = rows(llm_log, "llm", held_ids)
    llm_score = score_rows(items, llm_rows)
    tin = sum(r.get("tokens_in") or 0 for r in llm_rows)
    tout = sum(r.get("tokens_out") or 0 for r in llm_rows)
    est = (tin * LLM_PRICE_IN_PER_M + tout * LLM_PRICE_OUT_PER_M) / 1e6
    llm_score["cost_usd"] = {"reported": None, "note": "Ollama Cloud 訂閱制，不回傳單次費用；以下為 token × 假設單價估算",
                             "assumed_price_per_M": {"input": LLM_PRICE_IN_PER_M, "output": LLM_PRICE_OUT_PER_M},
                             "tokens_in": tin, "tokens_out": tout, "estimated_total": round(est, 6),
                             "estimated_per_call": round(est / len(llm_rows), 8) if llm_rows else None}
    llm_score["parse_errors"] = [r.get("error") for r in llm_log.rows.values() if r.get("error")]
    e2 = {"description": "同一批保留案例、同一份 state 與題目：純規則 vs Jev vs DeepSeek V4.1 Flash",
          "rules": score_rows(items, rule_rows) | {"method": "每個觸發事件類型取訓練案例最常見根因；誤報一律否；嚴重度＝規則判定",
                                                  "latency_ms": {"note": "查表 < 1 ms（未量測）"}},
          "jev": e1["first_run_only"], "jev_all_runs": e1["all_runs"],
          "llm": llm_score, "llm_model": C.SETTINGS.llm.model, "llm_repeats": LLM_REPEATS,
          "retrieval_majority_top1": e1["retrieval_majority_top1"]}
    C.save_json(f"e2_rules_jev_llm{C.embedder_tag()}.json", e2)

    # E3
    syn_ids = {it.item_id for it in syn}
    syn_rows = rows(jev_log, "synthetic", syn_ids)
    for r in syn_rows:
        r["latency_ms"] = (r.get("judgement") or {}).get("latency_ms")
    pts_held = calibration_points(items, base)
    pts_syn = calibration_points(items, syn_rows)
    e3 = {"description": "Jev 根因 top-1 機率分 5 組 vs 實際準確率",
          "heldout": calib_bins(pts_held), "synthetic": calib_bins(pts_syn),
          "combined": calib_bins(pts_held + pts_syn),
          "heldout_conf_field": calib_bins(calibration_points(items, base, "conf")),
          "llm_heldout": calib_bins(calibration_points(items, llm_rows)),
          "synthetic_note": f"合成：用 knowledge/_gen/case_skeleton.py Planner(seed={SYNTH_SEED}) 另外產生 {len(syn)} 筆沖壓事件骨架"
                            "（只有程式決定的 Log／脈絡與已知根因，沒有 LLM 文字），與入庫案例同分佈",
          "synthetic_scores": score_rows(items, syn_rows),
          "synthetic_case_types": dict(Counter(it.meta.get("case_type") for it in syn))}
    e1["synthetic_by_case_type"] = e3["synthetic_scores"]["root_cause_top1_by_case_type"]
    C.save_json(f"e3_calibration{C.embedder_tag()}.json", e3)
    return {"e1": e1, "e2": e2, "e3": e3}


# ── 壓力測試：無關訊號／複合原因 ─────────────────────────────────
NOISE_POOL = {
    "stamping": [("motor_temp_c", 52, 60), ("spm", 32, 40), ("air_pressure_bar", 5.6, 5.9), ("lube_pressure_bar", 2.0, 2.6),
                 ("bearing_temp_c", 38, 45), ("motor_current_a", 90, 120)],
    "welding": [("gas_flow_lpm", 16, 19), ("arc_voltage_v", 17.5, 19.5), ("coolant_flow_lpm", 4.5, 5.5),
                ("exhaust_flow_m3h", 1900, 2100), ("arc_current_a", 120, 140), ("wire_feed_speed_mpm", 5.8, 6.2)],
    "assembly": [("final_torque_nm", 0.78, 0.84), ("nok_rate_pct", 0.3, 0.8), ("insulation_mohm", 200, 500),
                 ("bit_cycle_count", 5000, 15000), ("feeder_pick_fail_count", 0, 2), ("final_angle_deg", 1200, 1600)],
}
NOISE_TEXT = ["叉車通過 B 區走道", "隔壁產線盤點，部分人員支援", "空調箱例行巡檢", "品保抽檢前一批外觀 OK"]


def noisy_items(items: list[C.EvalItem], seed: int = 99) -> list[C.EvalItem]:
    """在 state 塞入無關內容（Jev整合規劃.md §5 第 4 點）：鄰近機台的正常範圍訊號 6 筆＋2 筆無關文字。

    無關訊號同時進 facts（標 @鄰近機台）與時間線；時間線依時間插入，正式 build_state 只留最後 20 筆，
    所以真正的訊號可能被擠掉——這是刻意的壓力條件。
    """
    out = []
    for it in items:
        r = random.Random(f"{seed}:{it.item_id}")
        inc = C.EvalIncident(**{**it.incident.__dict__, "facts": dict(it.incident.facts)})
        tl = list(it.timeline)
        times = [x.split(" ")[0] for x in tl] or ["10:00:00"]
        for sig, lo, hi in r.sample(NOISE_POOL[it.line], 6):
            v = round(r.uniform(lo, hi), 2)
            inc.facts[f"{sig}@鄰近機台"] = {"value": v}
            tl.append(f"{r.choice(times)} {sig}={v}（鄰近機台，正常範圍）")
        for txt in r.sample(NOISE_TEXT, 2):
            tl.append(f"{r.choice(times)} {txt}")
        inc.facts["note"] = NOISE_TEXT[0]
        tl.sort(key=lambda x: x.split(" ")[0])
        out.append(C.EvalItem(it.item_id, it.line, it.case, inc, it.machine, tl, it.gt, it.synthetic,
                              meta=it.meta | {"stress": "noise"}))
    return out


def composite_items(kb, n: int = 20, seed: int = 4242) -> list[C.EvalItem]:
    """複合原因壓力集（合成）：強制 case_type=composite，主因＋一個 distinguish_from 的次因同時出現在 Log。"""
    gen = C.KNOWLEDGE / "_gen"
    sys.path.insert(0, str(gen))
    try:
        import case_skeleton as cs
    finally:
        sys.path.remove(str(gen))
    planner = cs.Planner(seed=seed)
    fms = [k for k, v in planner.cat.items() if v.get("distinguish_from")]
    rnd = random.Random(seed)
    out = []
    i = 0
    while len(out) < n and i < 200:
        fm = fms[i % len(fms)] if i < len(fms) else rnd.choice(fms)
        i += 1
        try:
            sk = planner.build(3000 + i, {"fm_id": fm, "case_type": "composite"})
        except RuntimeError:
            continue
        case = {"case_id": f"CMP-{seed}-{i:03d}", "line": "stamping", "machine_type": sk["machine_type"],
                "machine_id": sk["machine_id"], "trigger": sk["trigger"], "context": sk["context"],
                "log_signals": sk["log_signals"], "final_root_cause": sk["final_root_cause"], "outcome": sk["outcome"],
                "category": kb.failure_modes["stamping"][fm].get("category"), "case_type": "composite"}
        inc, m, tl = C.case_to_incident(case)
        out.append(C.EvalItem(case["case_id"], "stamping", case, inc, m, tl, C.expected(case, kb), synthetic=True,
                              meta={"case_type": "composite", "partner": (sk["contributing_causes"] or [None])[0]}))
    return out


async def run_cost_breakdown(items: list[C.EvalItem], log: RawLog) -> None:
    """同一份 state：4 題一次問 vs 只問 1 題（root_cause／category），比較每次請求的 token 與費用。"""
    kb = C.eval_kb()
    jev = C.jev_client()
    await jev.warmup()
    for it in items:
        fms = kb.failure_modes_for(it.line, it.machine.type)
        criteria = {fid: fm.get("criteria") or fm.get("label") or fid for fid, fm in fms.items()}
        full = Q.event_questions(criteria, kb.severity_notes(it.line, it.machine.type))
        cases = kb.similar_cases(it.incident, k=5, machine_type=it.machine.type)
        state = judge.build_state(it.incident, it.machine, it.timeline, cases)
        for name, qs in (("full4", full), ("root_cause_only", {"root_cause": full["root_cause"]}),
                         ("category_only", {"category": full["category"]}),
                         ("full4_no_cases", full)):
            key = f"costq:{name}:{it.item_id}"
            if log.has(key):
                continue
            st = state if name != "full4_no_cases" else {**state, "similar_cases": []}
            row: dict[str, Any] = {"key": key, "variant": "costq", "q": name, "item_id": it.item_id}
            try:
                res = await jev.ask(st, qs, purpose="eval.costq")
                row |= {"cost": res.cost, "input_tokens": res.input_tokens, "latency_ms": res.latency_ms}
            except JevUnavailable as e:
                row["error"] = str(e)
            log.add(row)
    await jev.aclose()


def lenient_composite(items: dict[str, C.EvalItem], rows: list[dict]) -> dict[str, Any]:
    k1 = k3 = n = 0
    for r in rows:
        it = items[r["item_id"]]
        top = [x["id"] for x in (r.get("judgement") or {}).get("root_cause_top3") or []]
        ok = {it.gt["root_cause"], it.meta.get("partner")}
        n += 1
        k1 += bool(top) and top[0] in ok
        k3 += it.gt["root_cause"] in top[:3] and it.meta.get("partner") in top[:3]
    return {"top1_is_primary_or_secondary": C.rate(k1, n), "both_causes_in_top3": C.rate(k3, n)}


def per_case_calibration(items: dict[str, C.EvalItem], rows: list[dict]) -> list[tuple[float, int]]:
    """每個案例只算一點：3 次 top-1 機率取平均、正確與否取多數決（重複呼叫不是獨立樣本）。"""
    by: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for p_y, r in zip(calibration_points(items, rows), [r for r in rows if r.get("judgement") and r["judgement"].get("root_cause_top3")
                                                        and items[r["item_id"]].gt["rc_in_catalog"]]):
        by[r["item_id"]].append(p_y)
    return [(statistics.fmean(p for p, _ in v), int(sum(y for _, y in v) * 2 > len(v))) for v in by.values()]


def analyze_extra(base_out: dict[str, Any]) -> dict[str, Any]:
    """壓力測試、成本拆解、per-case 校準；寫回 e1／e3 檔案。"""
    kb = C.eval_kb()
    held = C.heldout_items(kb)
    syn = synthetic_items(kb)
    comp = composite_items(kb)
    items = {it.item_id: it for it in held + syn + comp}
    log = RawLog(f"e1_jev{C.embedder_tag()}.jsonl")
    held_ids = {it.item_id for it in held}
    base = [r for r in log.rows.values() if r["variant"] == "base" and r["item_id"] in held_ids]
    rep0 = [r for r in base if r["rep"] == 0]
    noise = [r for r in log.rows.values() if r["variant"] == "noise"]
    compr = [r for r in log.rows.values() if r["variant"] == "composite"]
    for r in noise + compr:
        r["latency_ms"] = (r.get("judgement") or {}).get("latency_ms")
    e1 = base_out["e1"]
    e1["stress"] = {
        "noise": {"description": "保留集每筆塞入 6 筆鄰近機台正常訊號＋2 筆無關文字（facts 與時間線）；1 次",
                  "scores": score_rows(items, noise),
                  "top1_same_as_clean": C.rate(sum(1 for r in noise for c in rep0 if c["item_id"] == r["item_id"]
                                                   and (r.get("judgement") or {}).get("root_cause_top3")
                                                   and c["judgement"]["root_cause_top3"]
                                                   and r["judgement"]["root_cause_top3"][0]["id"] == c["judgement"]["root_cause_top3"][0]["id"]),
                                               len(noise))},
        "composite": {"description": "合成複合原因 20 筆（case_skeleton composite：主因＋次因訊號同時出現）；正確答案＝主因",
                      "scores": score_rows(items, compr), "lenient": lenient_composite(items, compr)},
        "synthetic_by_case_type": e1.get("synthetic_by_case_type"),
    }
    costq = [r for r in log.rows.values() if r["variant"] == "costq" and not r.get("error")]
    cb = {}
    for q in ("full4", "root_cause_only", "category_only", "full4_no_cases"):
        rs = [r for r in costq if r["q"] == q]
        if rs:
            cb[q] = {"n": len(rs), "cost_per_call": round(statistics.fmean(r["cost"] for r in rs), 8),
                     "input_tokens_mean": round(statistics.fmean(r["input_tokens"] for r in rs)),
                     "latency_ms": C.lat_stats([r["latency_ms"] for r in rs])}
    e1["cost_breakdown"] = cb | {"note": "同一批 10 筆保留案例；full4_no_cases＝4 題但 state 不帶相似案例"}
    dm = Counter((r.get("decision") or {}).get("diagnosis_mode") for r in rep0)
    e1["diagnosis_mode_first_run"] = {"counts": dict(dm), "threshold_root_cause_conf": C.SETTINGS.thresholds.root_cause_conf,
                                      "jev_top3_share": C.rate(dm.get("jev_top3", 0), len(rep0))}
    C.save_json(f"e1_root_cause_jev{C.embedder_tag()}.json", e1)
    e3 = base_out["e3"]
    e3["heldout_first_run"] = calib_bins(calibration_points(items, rep0))
    e3["heldout_per_case_mean"] = calib_bins(per_case_calibration(items, base))
    e3["n_distinct_cases_heldout"] = len({r["item_id"] for r in rep0 if items[r["item_id"]].gt["rc_in_catalog"]})
    C.save_json(f"e3_calibration{C.embedder_tag()}.json", e3)
    return e1


async def run_all(skip_llm: bool = False) -> None:
    kb = C.eval_kb()
    tag = C.embedder_tag()
    held = C.heldout_items(kb)
    syn = synthetic_items(kb)
    log = RawLog(f"e1_jev{tag}.jsonl")
    print(f"embedder {kb.store.embedder.name if kb.store else None}；保留測試集 {len(held)} 筆；合成 {len(syn)} 筆", flush=True)
    await run_jev(held, log, variant="base", repeats=REPEATS)
    await run_jev(held, log, variant="perm", repeats=1)
    await run_jev(syn, log, variant="synthetic", repeats=1)
    await run_jev(noisy_items(held), log, variant="noise", repeats=1)
    await run_jev(composite_items(kb), log, variant="composite", repeats=1)
    await run_cost_breakdown(held[:: 4][:10], log)
    print(f"Jev 完成：{len(log.rows)} 筆", flush=True)
    if not skip_llm:
        llog = RawLog(f"e2_llm{tag}.jsonl")
        await run_llm(held, llog)
        print(f"LLM 完成：{len(llog.rows)} 筆", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyze"])
    ap.add_argument("--skip-llm", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        asyncio.run(run_all(a.skip_llm))
    res = analyze()
    analyze_extra(res)
    e1, e2 = res["e1"]["first_run_only"], res["e2"]
    print(json.dumps({"jev_top1": e1["root_cause_top1"], "llm_top1": e2["llm"]["root_cause_top1"],
                      "rules_top1": e2["rules"]["root_cause_top1"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
