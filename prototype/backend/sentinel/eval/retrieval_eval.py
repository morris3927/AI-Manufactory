"""R1 相似案例檢索基準（不打 API）。

每筆保留案例（cases/**/heldout，40 筆）用 eval/common.case_to_incident 轉成正式流程看到的 Incident，
呼叫正式 KnowledgeBase.similar_cases(k=5, machine_type)（與 dispatch 相同），從非 heldout 的知識庫取 5 筆，量：
  hit@1／hit@5：取回案例的 failure_mode_id（＝該案例 final_root_cause）是否等於保留案例的 final_root_cause
  MRR@5、取回案例中最終根因為 none／new／false_alarm 的比例、每次查詢延遲（筆電 CPU）
主指標只算根因在故障模式目錄內的 37 筆；none（誤報）2 筆、new 1 筆另列。

用法（backend/ 底下）：
  python -m sentinel.eval.retrieval_eval                     # 全部 embedder × 設定，寫 eval/results/r1_retrieval.json
  python -m sentinel.eval.retrieval_eval --embedders e5-small --grid   # 只對一個 embedder 掃加減分權重
"""
from __future__ import annotations

import argparse
import functools
import itertools
import json
import statistics
import time
from collections import defaultdict
from typing import Any

from ..kb import ingest as ing
from ..kb.retrieve import KnowledgeBase, RetrievalCfg
from ..kb.store import HashEmbedder, SentenceTransformerEmbedder, VectorStore
from . import common as C

EMBEDDERS = {
    "hash-512": None,
    "e5-small": "intfloat/multilingual-e5-small",
    "e5-base": "intfloat/multilingual-e5-base",
    "bge-m3": "BAAI/bge-m3",
}
BAD = {"none", "new"}

_NOMETA = {"boost_trigger": 0, "boost_signals": 0, "boost_machine_type": 0, "penalty_non_root_cause": 0}
# 依序加上改進（每一列只比上一列多一項，方便看各自的效果）
CONFIGS: dict[str, dict[str, Any]] = {
    "legacy": {"index": "summary", "cfg": {"mode": "legacy", "query_format": "legacy"}},
    "vector": {"index": "summary", "cfg": {"mode": "vector", "query_format": "legacy", **_NOMETA}},
    "vector+structured": {"index": "structured", "cfg": {"mode": "vector", **_NOMETA}},
    "hybrid": {"index": "structured", "cfg": {"mode": "hybrid", **_NOMETA}},
    "hybrid+mtfilter": {"index": "structured", "cfg": {"mode": "hybrid", "filter_machine_type": True,
                                                       "boost_trigger": 0, "boost_signals": 0, "boost_machine_type": 0,
                                                       "penalty_non_root_cause": 0}},
    "hybrid+meta": {"index": "structured", "cfg": {"mode": "hybrid", "penalty_non_root_cause": 0}},
    "hybrid+meta+downweight": {"index": "structured", "cfg": {"mode": "hybrid"}},          # ＝ RetrievalCfg 預設
    "vector+meta+downweight": {"index": "structured", "cfg": {"mode": "vector"}},
}


def make_store(emb_key: str, index: str) -> VectorStore:
    model = EMBEDDERS[emb_key]
    emb = HashEmbedder() if model is None else SentenceTransformerEmbedder(model)
    path = C.CACHE / "retrieval" / f"chroma-{emb_key}-{index}"
    store = VectorStore(path, emb, index_text=index)
    marker = path.with_name(path.name + ".ok")
    if not marker.exists() or store.count("cases") == 0:
        t0 = time.perf_counter()
        store.reset()
        chunks, _ = ing.collect_chunks(C.KNOWLEDGE, allow_draft=True)
        store.upsert("cases", chunks["cases"])        # 只需要 cases collection
        marker.write_text(json.dumps({"embedder": emb.name, "index_text": index,
                                      "build_s": round(time.perf_counter() - t0, 1)}))
    return store


def evaluate(kb: KnowledgeBase, items: list[C.EvalItem], k: int = 5) -> dict[str, Any]:
    kb.similar_cases(items[0].incident, k=k, machine_type=items[0].machine.type)      # 暖機（載入模型、建快取）
    per: list[dict[str, Any]] = []
    lat: list[float] = []
    for it in items:
        t0 = time.perf_counter()
        hits = kb.similar_cases(it.incident, k=k, machine_type=it.machine.type)
        lat.append((time.perf_counter() - t0) * 1000)
        fms = [h.failure_mode_id for h in hits]
        gt = it.gt["root_cause"]
        rank = next((i + 1 for i, f in enumerate(fms) if f == gt), None)
        outcomes = [_OUTCOME.get(h.case_id) for h in hits]
        per.append({"item": it.item_id, "line": it.line, "gt": gt, "in_catalog": it.gt["rc_in_catalog"],
                    "retrieved": [h.case_id for h in hits], "retrieved_fm": fms, "rank": rank,
                    "bad": sum(1 for f, o in zip(fms, outcomes) if f in BAD or o == "false_alarm")})

    def agg(rows: list[dict]) -> dict[str, Any]:
        sc = [r for r in rows if r["in_catalog"]]
        n = len(sc)
        nret = sum(len(r["retrieved"]) for r in rows) or 1
        return {"n": n, "hit@1": C.rate(sum(r["rank"] == 1 for r in sc), n),
                "hit@5": C.rate(sum(r["rank"] is not None for r in sc), n),
                "mrr": round(sum(1 / r["rank"] for r in sc if r["rank"]) / n, 3) if n else None,
                "bad_share": round(sum(r["bad"] for r in rows) / nret, 3)}

    by_line = defaultdict(list)
    for r in per:
        by_line[r["line"]].append(r)
    off = [r for r in per if not r["in_catalog"]]
    return {"all": agg(per), "by_line": {ln: agg(rs) for ln, rs in sorted(by_line.items())},
            "non_catalog": [{"item": r["item"], "gt": r["gt"], "retrieved_fm": r["retrieved_fm"]} for r in off],
            "latency_ms": C.lat_stats(lat), "per_item": per}


_OUTCOME: dict[str, str | None] = {}


def _brief(res: dict[str, Any]) -> str:
    a = res["all"]
    return (f"hit@1 {a['hit@1']['rate']:.3f} hit@5 {a['hit@5']['rate']:.3f} MRR {a['mrr']:.3f} "
            f"bad {a['bad_share']:.2f} p50 {res['latency_ms']['p50']} ms p95 {res['latency_ms']['p95']} ms")


def grid(emb_key: str, items: list[C.EvalItem]) -> list[dict[str, Any]]:
    store = make_store(emb_key, "structured")
    store.embed_query = functools.lru_cache(maxsize=None)(store.embed_query)    # 掃參數時查詢向量不用重算
    out = []
    for mode, wb, bt, bs, bm, pn in itertools.product(["vector", "hybrid"], [0.5, 1.0, 2.0], [0, 0.5, 1, 2],
                                                      [0, 1, 2], [0, 0.25, 0.5, 1], [0, 0.5, 1, 2]):
        if mode == "vector" and wb != 1.0:
            continue
        cfg = RetrievalCfg(mode=mode, w_bm25=wb, boost_trigger=bt, boost_signals=bs, boost_machine_type=bm,
                           penalty_non_root_cause=pn)
        r = evaluate(KnowledgeBase(C.KNOWLEDGE, store, allow_draft=True, retrieval=cfg), items)
        out.append({"cfg": cfg.model_dump(), "all": r["all"]})
    out.sort(key=lambda x: (-x["all"]["hit@5"]["rate"], -x["all"]["mrr"], -x["all"]["hit@1"]["rate"]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedders", default=",".join(EMBEDDERS))
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--out", default="r1_retrieval.json")
    a = ap.parse_args(argv)
    kb0 = KnowledgeBase(C.KNOWLEDGE, None, allow_draft=True)
    items = C.heldout_items(kb0)
    _OUTCOME.update({c["case_id"]: c.get("outcome") for c in ing.load_cases(C.KNOWLEDGE)})
    if a.grid:
        for emb_key in a.embedders.split(","):
            g = grid(emb_key, items)
            C.save_json(f"r1_grid_{emb_key}.json", g)
            for x in g[:8]:
                c = x["cfg"]
                print(emb_key, c["mode"], c["w_bm25"], c["boost_trigger"], c["boost_signals"], c["boost_machine_type"],
                      c["penalty_non_root_cause"],
                      x["all"]["hit@1"]["rate"], x["all"]["hit@5"]["rate"], x["all"]["mrr"], x["all"]["bad_share"])
        return 0
    results: dict[str, Any] = {"description": __doc__.split("\n\n")[0], "n_heldout": len(items), "k": 5,
                               "default_cfg": RetrievalCfg().model_dump(), "runs": {}}
    prev = C.load_json(a.out) or {}
    results["runs"] = prev.get("runs", {})
    for emb_key in a.embedders.split(","):
        for name in a.configs.split(","):
            spec = CONFIGS[name]
            try:
                store = make_store(emb_key, spec["index"])
            except Exception as e:                  # 模型還沒下載完等
                print(f"{emb_key}: 無法載入（{e}）")
                break
            kb = KnowledgeBase(C.KNOWLEDGE, store, allow_draft=True, retrieval=spec["cfg"])
            res = evaluate(kb, items)
            results["runs"][f"{emb_key}|{name}"] = {"embedder": emb_key, "config": name, "index_text": spec["index"],
                                                     "cfg": kb.retrieval.model_dump(), **res}
            print(f"{emb_key:9s} {name:24s} {_brief(res)}", flush=True)
    C.save_json(a.out, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
