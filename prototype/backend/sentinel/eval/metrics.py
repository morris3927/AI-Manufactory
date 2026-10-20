"""效益指標（UI-5、EV-4）：反應時間、MTTR、AI 成本、規則與 AI 處理比例、校準曲線。"""
from __future__ import annotations

import statistics
from typing import Any

from sqlmodel import select

from ..db.models import Diagnosis, Incident, JevCall, LlmCall, Notification, Workorder
from ..db.session import Database


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 3)


def _stats(vals: list[float]) -> dict[str, Any]:
    return {"n": len(vals), "mean": round(statistics.fmean(vals), 3) if vals else None,
            "p50": _pct(vals, 0.5), "p95": _pct(vals, 0.95)}


def _mean(vals: list[float]) -> float:
    return round(statistics.fmean(vals), 3) if vals else 0.0


def summary(db: Database) -> dict[str, Any]:
    """前端 MetricsSummary 格式；完整統計放在 detail。"""
    d = detail(db)
    jev = db.all(select(JevCall).order_by(JevCall.ts))
    llm = db.all(select(LlmCall))
    series, acc = [], 0.0
    for c in jev:
        acc += c.cost or 0
        series.append({"ts": c.ts.isoformat(), "cost": round(acc, 6)})
    ok_jev = [c for c in jev if not c.error]
    ok_llm = [c for c in llm if not c.error]
    lat = d["latency_s"]
    return {
        "reaction_s": {"detect": d["detect_s"]["mean"] or 0.0, "notify": lat["detect_to_notify"]["mean"] or 0.0,
                       "arrive": lat["notify_to_ack"]["mean"] or 0.0},
        "mttr_min": d["mttr_min"]["mean"] or 0.0,
        "ai_cost_usd": d["ai_cost_usd"]["jev"],
        "ai_cost_series": series[-200:],
        "handled": {"rules_only": d["handling"]["rules_only"], "jev": d["handling"]["jev_top3"],
                    "llm": d["handling"]["llm_deep"]},
        "severity_counts": d["incidents"]["by_severity"],
        "incidents_total": d["incidents"]["total"],
        "open_workorders": sum(v for k, v in d["workorders"]["by_status"].items() if k not in ("CLOSED", "CANCELLED")),
        "comparison": [
            {"method": "規則", "latency_ms": 1.0, "accuracy": None, "cost_per_call_usd": 0.0,
             "calls": d["incidents"]["total"]},
            {"method": "Jev", "latency_ms": _mean([c.latency_ms for c in ok_jev if c.latency_ms is not None]),
             "accuracy": None, "cost_per_call_usd": round(sum(c.cost or 0 for c in ok_jev) / len(ok_jev), 8) if ok_jev else 0.0,
             "calls": len(jev)},
            {"method": "LLM", "latency_ms": _mean([c.latency_ms for c in ok_llm if c.latency_ms is not None]),
             "accuracy": None, "cost_per_call_usd": 0.0, "calls": len(llm)},   # Ollama Cloud 不回傳費用
        ],
        "detail": d,
    }


def detail(db: Database) -> dict[str, Any]:
    incs = db.all(select(Incident))
    wos = {w.incident_id: w for w in db.all(select(Workorder))}
    notes = db.all(select(Notification).where(Notification.kind == "dispatch"))
    first_note: dict[str, Any] = {}
    for n in notes:
        if n.workorder_id not in first_note or n.ts < first_note[n.workorder_id]:
            first_note[n.workorder_id] = n.ts
    detect_to_notify, ack, mttr, detect = [], [], [], []
    for inc in incs:
        sec = (inc.facts or {}).get("detect_latency_s")         # 訊號變化 → 規則成立（開單當下的值）
        if isinstance(sec, (int, float)):
            detect.append(float(sec))
        wo = wos.get(inc.id)
        if wo and wo.id in first_note:
            detect_to_notify.append((first_note[wo.id] - inc.opened_at).total_seconds())
        if wo and wo.acked_at:
            ack.append((wo.acked_at - wo.created_at).total_seconds())
        if inc.closed_at:
            mttr.append((inc.closed_at - inc.opened_at).total_seconds() / 60)
    jev = db.all(select(JevCall))
    llm = db.all(select(LlmCall))
    diags = db.all(select(Diagnosis))
    modes: dict[str, int] = {}
    for d in diags:
        modes[d.mode] = modes.get(d.mode, 0) + 1
    by_sev: dict[str, int] = {}
    for i in incs:
        by_sev[i.final_severity] = by_sev.get(i.final_severity, 0) + 1
    by_status: dict[str, int] = {}
    for w in wos.values():
        by_status[w.status] = by_status.get(w.status, 0) + 1
    ok_jev = [c for c in jev if not c.error]
    return {
        "incidents": {"total": len(incs), "by_severity": by_sev,
                      "suspected_false_alarm": sum(1 for i in incs if i.suspected_false_alarm)},
        "workorders": {"total": len(wos), "by_status": by_status},
        "detect_s": _stats(detect),
        "latency_s": {"detect_to_notify": _stats(detect_to_notify), "notify_to_ack": _stats(ack)},
        "mttr_min": _stats(mttr),
        "ai_cost_usd": {"jev": round(sum(c.cost or 0 for c in jev), 6), "jev_calls": len(jev),
                        "jev_errors": len(jev) - len(ok_jev), "llm_calls": len(llm),
                        "llm_tokens": sum(c.tokens or 0 for c in llm)},
        "jev_latency_ms": _stats([c.latency_ms for c in ok_jev if c.latency_ms is not None]),
        "handling": {"rules_only": modes.get("rules_only", 0), "jev_top3": modes.get("jev_top3", 0),
                     "llm_deep": modes.get("llm_deep", 0)},
    }


def calibration(db: Database, bins: int = 5) -> dict[str, Any]:
    """Jev 根因 top-1 機率分組 vs 實際準確率（結案時回填 final_root_cause 的案件）；附 Brier 與 ECE。"""
    rows = [d for d in db.all(select(Diagnosis)) if d.final_root_cause and d.root_cause_top3]
    pts = [(float(d.root_cause_top3[0]["p"]), 1.0 if d.root_cause_top3[0]["id"] == d.final_root_cause else 0.0)
           for d in rows]
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        grp = [(p, y) for p, y in pts if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if grp:
            out.append({"p_pred": round(statistics.fmean(p for p, _ in grp), 4),
                        "p_obs": round(statistics.fmean(y for _, y in grp), 4), "n": len(grp), "bin": [lo, hi]})
    n = len(pts)
    brier = round(sum((p - y) ** 2 for p, y in pts) / n, 4) if n else None
    ece = round(sum(b["n"] / n * abs(b["p_pred"] - b["p_obs"]) for b in out), 4) if n else None
    return {"bins": out, "brier": brier, "ece": ece, "n_total": n}
