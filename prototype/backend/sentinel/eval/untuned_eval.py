"""U1 未參與調參的驗證集：凍結的檢索設定（config.yaml kb.retrieval，不再調整）在「沒用來調權重」的資料上的表現。

R1 的加減分權重是在 40 筆保留案例上手調的 → 那組數字偏樂觀。這裡換兩組沒看過的資料：
  synthetic：E3 合成集（knowledge/_gen/case_skeleton.py Planner(seed=7777)，沖壓 80 筆，扣掉 new）
  fresh：新 seed 另抽 60 筆骨架（沖壓 Planner(seed=8888)、銲接／組裝 case_skeleton_wa.Planner 換亂數 9101／9102，各 20 筆，扣掉 new）
骨架只有程式決定的 Log／脈絡與已知根因（沒有 LLM 文字），與入庫案例同一個產生器 → 同分佈合成測試，不等於真實工廠資料。

每組、每個 embedder（bge-m3、e5-small）量：檢索 hit@1／hit@5／MRR（retrieval_eval 同定義）、
Jev J1–J3 根因 Top-1（Wilson 95% CI，1 次）與校準 ECE。

用法（backend/ 底下）：python -m sentinel.eval.untuned_eval [run|analyze]
原始回應 eval/results/raw/u1_jev_<embedder>.jsonl，指標 eval/results/u1_untuned.json
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from collections import Counter
from typing import Any

from ..jev import judge
from ..jev.client import JevUnavailable
from ..jev.gate import decide
from ..kb.retrieve import KnowledgeBase
from . import common as C
from . import judge_eval as J
from . import retrieval_eval as R

EMBEDDERS = ["bge-m3", "e5-small"]
FRESH_SEEDS = {"stamping": 8888, "welding": 9101, "assembly": 9102}
FRESH_PER_LINE = 20


def _import_gen():
    gen = str(C.KNOWLEDGE / "_gen")
    sys.path.insert(0, gen)
    try:
        import case_skeleton as cs        # noqa: WPS433（只讀匯入）
        import case_skeleton_wa as cw
    finally:
        sys.path.remove(gen)
    return cs, cw


def fresh_items(kb: KnowledgeBase, per_line: int = FRESH_PER_LINE) -> list[C.EvalItem]:
    cs, cw = _import_gen()
    out: list[C.EvalItem] = []
    for line, seed in FRESH_SEEDS.items():
        if line == "stamping":
            planner = cs.Planner(seed=seed)
        else:
            planner = cw.Planner(line)
            planner.r = random.Random(seed)                     # 換掉產生入庫案例的固定亂數
            planner.pm_offset = {m: planner.r.randint(0, planner.pm - 1) for m in planner.machines}
        plans = planner.plan()
        random.Random(seed).shuffle(plans)
        n = 0
        for i, p in enumerate(plans):
            if n >= per_line:
                break
            if p["case_type"] == "new":
                continue
            try:
                sk = planner.build(5000 + i, p)
            except RuntimeError:
                continue
            fm = sk.get("fm_id") or p["fm_id"]
            case = {"case_id": f"FRESH-{line[:3].upper()}-{seed}-{i:03d}", "line": line,
                    "machine_type": sk["machine_type"], "machine_id": sk["machine_id"], "trigger": sk["trigger"],
                    "context": sk["context"], "log_signals": sk["log_signals"],
                    "final_root_cause": sk["final_root_cause"], "outcome": sk["outcome"],
                    "category": kb.failure_modes[line][fm].get("category"), "case_type": sk["case_type"]}
            inc, m, tl = C.case_to_incident(case)
            out.append(C.EvalItem(case["case_id"], line, case, inc, m, tl, C.expected(case, kb), synthetic=True,
                                  meta={"case_type": sk["case_type"], "set": "fresh"}))
            n += 1
    return out


def item_sets(kb: KnowledgeBase) -> dict[str, list[C.EvalItem]]:
    syn = J.synthetic_items(kb)
    for it in syn:
        it.meta["set"] = "synthetic"
    return {"synthetic": syn, "fresh": fresh_items(kb)}


def frozen_kb(emb: str) -> KnowledgeBase:
    return KnowledgeBase(C.KNOWLEDGE, R.make_store(emb, C.SETTINGS.kb.index_text), allow_draft=True,
                         retrieval=C.SETTINGS.kb.retrieval)


async def run_jev(kb: KnowledgeBase, items: list[C.EvalItem], log: J.RawLog, conc: int = 4) -> None:
    jev = C.jev_client()
    await jev.warmup()
    sem = asyncio.Semaphore(conc)

    async def one(it: C.EvalItem) -> None:
        key = f"u1:{it.item_id}"
        if log.has(key):
            return
        fms = kb.failure_modes_for(it.line, it.machine.type)
        cases = kb.similar_cases(it.incident, k=5, machine_type=it.machine.type)
        async with sem:
            row: dict[str, Any] = {"key": key, "variant": it.meta["set"], "item_id": it.item_id, "ts": time.time(),
                                   "similar_cases": [c.case_id for c in cases],
                                   "similar_fm": [c.failure_mode_id for c in cases]}
            try:
                j = await judge.event(jev, it.incident, machine=it.machine, timeline=it.timeline, similar_cases=cases,
                                      failure_modes=fms, severity_notes=kb.severity_notes(it.line, it.machine.type))
                row["judgement"] = j.model_dump()
                row["decision"] = decide(it.incident.rule_severity, j,
                                         p_false_alarm=C.SETTINGS.thresholds.p_false_alarm,
                                         root_cause_conf=C.SETTINGS.thresholds.root_cause_conf).model_dump()
            except JevUnavailable as e:
                row["error"] = str(e)
            log.add(row)

    await asyncio.gather(*(one(it) for it in items))
    await jev.aclose()


def analyze() -> dict[str, Any]:
    kb0 = KnowledgeBase(C.KNOWLEDGE, None, allow_draft=True)
    sets = item_sets(kb0)
    R._OUTCOME.update({c["case_id"]: c.get("outcome") for c in J.ing.load_cases(C.KNOWLEDGE)})
    out: dict[str, Any] = {"description": __doc__.split("\n\n")[0], "frozen_retrieval": KnowledgeBase(C.KNOWLEDGE, None, retrieval=C.SETTINGS.kb.retrieval).retrieval.model_dump(),
                           "embedding_model_default": C.SETTINGS.kb.embedding_model,
                           "sets": {k: {"n": len(v), "by_line": dict(Counter(it.line for it in v)),
                                        "case_types": dict(Counter(it.meta.get("case_type") for it in v))}
                                    for k, v in sets.items()},
                           "results": {}}
    for emb in EMBEDDERS:
        kb = frozen_kb(emb)
        log = J.RawLog(f"u1_jev_{emb}.jsonl")
        for name, its in list(sets.items()) + [("combined", sets["synthetic"] + sets["fresh"])]:
            items = {it.item_id: it for it in its}
            rows = [r for r in log.rows.values() if r["item_id"] in items]
            for r in rows:
                r["latency_ms"] = (r.get("judgement") or {}).get("latency_ms")
            ret = R.evaluate(kb, its)
            legacy = R.evaluate(KnowledgeBase(C.KNOWLEDGE, R.make_store(emb, "summary"), allow_draft=True,
                                              retrieval=R.CONFIGS["legacy"]["cfg"]), its)     # 舊版檢索對照
            sc = J.score_rows(items, rows) if rows else {}
            cal = J.calib_bins(J.calibration_points(items, rows)) if rows else {}
            out["results"][f"{emb}|{name}"] = {
                "retrieval": {"all": ret["all"], "by_line": ret["by_line"], "latency_ms": ret["latency_ms"]},
                "retrieval_legacy": {"all": legacy["all"], "by_line": legacy["by_line"]},
                "jev": {k: sc.get(k) for k in ("n_calls", "n_errors", "root_cause_top1", "root_cause_top3",
                                               "root_cause_by_line", "root_cause_top1_by_case_type", "category",
                                               "cost_usd")} if sc else None,
                "calibration": cal,
                "diagnosis_mode": dict(Counter((r.get("decision") or {}).get("diagnosis_mode") for r in rows)),
            }
    C.save_json("u1_untuned.json", out)
    return out


async def run_all() -> None:
    kb0 = KnowledgeBase(C.KNOWLEDGE, None, allow_draft=True)
    sets = item_sets(kb0)
    items = sets["synthetic"] + sets["fresh"]
    for emb in EMBEDDERS:
        await run_jev(frozen_kb(emb), items, J.RawLog(f"u1_jev_{emb}.jsonl"))
        print(f"{emb}：Jev {len(items)} 筆完成", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "analyze"], nargs="?", default="analyze")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        asyncio.run(run_all())
    res = analyze()
    for k, v in res["results"].items():
        r, j, c = v["retrieval"]["all"], v["jev"] or {}, v["calibration"] or {}
        lg = v["retrieval_legacy"]["all"]
        print(f"{k:22s} legacy hit@1 {lg['hit@1']['rate']} hit@5 {lg['hit@5']['rate']} MRR {lg['mrr']} | hit@1 {r['hit@1']['rate']} hit@5 {r['hit@5']['rate']} MRR {r['mrr']} | "
              f"Jev top1 {(j.get('root_cause_top1') or {}).get('rate')} {(j.get('root_cause_top1') or {}).get('ci95')} "
              f"ECE {c.get('ece')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
