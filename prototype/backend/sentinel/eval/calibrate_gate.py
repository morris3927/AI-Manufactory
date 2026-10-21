"""J4 安全把關門檻校準：對 gate_set.yaml 的每個步驟呼叫真實 Jev，選出各題門檻。

規則：危險步驟（任一標籤為 true）召回率必須 1.0；在這個前提下讓一般步驟的誤標最少；
同樣誤標數時取最低門檻（留安全餘裕）。needs_loto 在 LOTO_DONE 後只記錄，不參與。

用法：python -m sentinel.eval.calibrate_gate [--from-cache]   （約 32 次 Jev 呼叫，< US$0.001）
結果寫到 sentinel/eval/gate_calibration.json；選出的門檻要手動抄進 config.yaml 的 thresholds.gate。
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
from pathlib import Path

import yaml

HERE = Path(__file__).parent
QUESTIONS = ("enters_danger_zone", "high_voltage", "energized")
LABEL = {"enters_danger_zone": "danger_zone", "high_voltage": "high_voltage", "energized": "energized"}
GRID = [round(0.05 * i, 2) for i in range(1, 19)]          # 0.05 … 0.90


def load_set() -> list[dict]:
    return yaml.safe_load((HERE / "gate_set.yaml").read_text(encoding="utf-8"))["items"]


def dangerous(item: dict) -> bool:
    return any(item.get(LABEL[q]) for q in QUESTIONS)


def evaluate(rows: list[dict], th: dict[str, float]) -> dict:
    tp = fp = fn = tn = 0
    for r in rows:
        flag = any(r["p"][q] > th[q] for q in QUESTIONS)
        pos = dangerous(r)
        tp += flag and pos
        fp += flag and not pos
        fn += (not flag) and pos
        tn += (not flag) and not pos
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "recall": tp / (tp + fn) if tp + fn else None,
            "precision": tp / (tp + fp) if tp + fp else None}


def choose(rows: list[dict]) -> tuple[dict[str, float], dict]:
    """每題：門檻放在「一般步驟最高機率」到「該標籤危險步驟最低機率」之間靠下 1/3 處（偏向安全），取 0.05 的倍數。
    檢查整體召回率 1.0；分不開或召回不足時，改用格點搜尋（誤標最少，同分取最低門檻）。"""
    import math

    th: dict[str, float] = {}
    for q in QUESTIONS:
        pos = [r["p"][q] for r in rows if r.get(LABEL[q])]
        neg = [r["p"][q] for r in rows if not dangerous(r)]
        lo, hi = max(neg, default=0.0), min(pos, default=1.0)
        th[q] = max(0.05, math.floor((lo + (hi - lo) / 3) * 20) / 20) if hi > lo else 0.0
    m = evaluate(rows, th)
    if all(th.values()) and not m["fn"]:
        return th, m
    best = None
    for combo in itertools.product(GRID, repeat=len(QUESTIONS)):
        cand = dict(zip(QUESTIONS, combo))
        mm = evaluate(rows, cand)
        if mm["fn"]:
            continue
        key = (mm["fp"], max(combo), sum(combo))
        if best is None or key < best[0]:
            best = (key, cand, mm)
    assert best is not None, "沒有任何門檻組合能讓召回率達到 1.0"
    return best[1], best[2]


async def collect() -> list[dict]:
    from ..config import load_settings
    from ..jev import gate
    from ..jev.client import JevClient

    s = load_settings()
    key = s.secret(s.secrets.openrouter_api_key)
    if not key:
        raise SystemExit("需要 OPENROUTER_API_KEY")
    jev = JevClient(api_key=key, url=s.jev.url, model=s.jev.model, timeout_s=10)
    try:
        sem = asyncio.Semaphore(4)

        async def one(it: dict) -> dict:
            async with sem:
                g = await gate.step(jev, it["text"], it.get("machine_type"), loto_done=True)
            return {**it, "p": {q: getattr(g, q) for q in (*QUESTIONS, "needs_loto")}}

        return list(await asyncio.gather(*(one(it) for it in load_set())))
    finally:
        await jev.aclose()


def main() -> None:
    from ..jev.questions import GATE_VER

    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true", help="用上次的 Jev 機率重新計算門檻，不呼叫 API")
    args = ap.parse_args()
    out_path = HERE / "gate_calibration.json"
    rows = json.loads(out_path.read_text())["rows"] if args.from_cache else asyncio.run(collect())
    th, m = choose(rows)
    old = evaluate(rows, {q: 0.3 for q in QUESTIONS})
    out_path.write_text(json.dumps({"questions_ver": GATE_VER, "thresholds": th, "metrics": m,
                                    "baseline_0.3": old, "rows": rows}, ensure_ascii=False, indent=1), "utf-8")
    print(f"題目版本 {GATE_VER}；n={len(rows)}（危險 {sum(map(dangerous, rows))}）")
    for r in rows:
        mark = "危險" if dangerous(r) else "一般"
        print(f"  {r['id']} {mark} " + " ".join(f"{q[:6]}={r['p'][q]:.2f}" for q in (*QUESTIONS, "needs_loto"))
              + f"  {r['text'][:28]}")
    print(f"選定門檻：{th}")
    print(f"結果：recall={m['recall']:.2f} precision={m['precision']:.2f}（FP {m['fp']}／一般 {m['fp'] + m['tn']}）")
    print(f"對照（全部 0.3）：recall={old['recall']:.2f} precision={old['precision']:.2f} FP {old['fp']}")


if __name__ == "__main__":
    main()
