"""E6：端到端延遲（真實 Jev／LLM API，經過完整後端）。

在本機另外啟動一份後端（預設 port 8010，資料庫與關鍵幀放在 eval/.cache/，不動正式 sentinel.db），
透過 /ws 收所有事件、/api/scenarios/{id}/run 觸發劇本；每個劇本跑 N 次，結束後關閉後端。

分段（feed_jam）：
  (1) 異常開始 → 規則成立（incident_opened）：分別以「誤差開始爬升 t=8」「畫面開始卡料 t=10」「MISFEED／送料歸零 t=14」為起點
  (2) 規則成立 → 派工通知送出（notification_sent，console 通知器；沒有接 Discord）
  (3) 通知送出 → LLM 說明補上（llm_explanation）
另外用 keyframe_interval_s = 2 再跑一輪，比較 (1)。
PPE：違規畫面開始（t=5）→ ppe_violation 事件 → 規則成立 → 通知送出。

用法：python -m sentinel.eval.e2e_eval [--runs 5] [--port 8010]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import websockets
import yaml

from ..config import BACKEND_DIR
from . import common as C

E2E = C.CACHE / "e2e"


def write_config(variant: str, keyframe_interval_s: float) -> Path:
    data = yaml.safe_load((BACKEND_DIR / "config.yaml").read_text(encoding="utf-8"))
    d = E2E / variant
    d.mkdir(parents=True, exist_ok=True)
    data["paths"].update({"db": str(d / "sentinel.db"), "chroma": str(C.CACHE / "chroma"),
                          "keyframes": str(d / "keyframes"), "drafts": str(d / "drafts")})
    data["vision"]["keyframe_interval_s"] = keyframe_interval_s
    data["kb"]["allow_draft"] = True
    data["kb"]["embedding"] = "hash"          # 與 eval/.cache/chroma 一致；避免另一個 venv 變動中途換 embedder
    p = d / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


class Server:
    def __init__(self, cfg: Path, port: int):
        self.cfg, self.port = cfg, port
        self.proc: subprocess.Popen | None = None
        self.base = f"http://127.0.0.1:{port}"

    def __enter__(self) -> "Server":
        env = os.environ | {"SENTINEL_CONFIG": str(self.cfg)}
        logf = open(self.cfg.parent / "server.log", "w")
        self.proc = subprocess.Popen([str(BACKEND_DIR / ".venv/bin/uvicorn"), "sentinel.main:app", "--port", str(self.port),
                                      "--host", "127.0.0.1"], cwd=BACKEND_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        for _ in range(120):
            try:
                if httpx.get(self.base + "/api/scenarios", timeout=2).status_code == 200:
                    time.sleep(3)             # 等 Jev 暖機
                    return self
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"後端啟動失敗，見 {self.cfg.parent / 'server.log'}")
            time.sleep(1)
        raise RuntimeError("後端啟動逾時")

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)


def _ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


async def one_run(srv: Server, scenario: str, wait_s: float) -> dict[str, Any]:
    msgs: list[dict] = []
    async with httpx.AsyncClient(base_url=srv.base, timeout=30) as http:
        await http.post("/api/demo/reset")
        await asyncio.sleep(2)
        async with websockets.connect(f"ws://127.0.0.1:{srv.port}/ws", max_size=None) as ws:
            async def reader() -> None:
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("channel") == "metrics":
                        continue
                    m["recv"] = time.time()
                    msgs.append(m)
            task = asyncio.create_task(reader())
            r = (await http.post(f"/api/scenarios/{scenario}/run")).json()
            t0 = _ts(r["started_at"])
            await asyncio.sleep(wait_s)
            task.cancel()
    return {"scenario": scenario, "t0": t0, "messages": msgs}


def db_calls(db: Path, t0: float, t1: float) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[str, Any] = {"jev": [], "llm": []}
    for table, key, cols in (("jev_call", "jev", "purpose, latency_ms, cost, ts, error"),
                             ("llm_call", "llm", "purpose, latency_ms, tokens, ts, error")):
        try:
            rows = con.execute(f"select {cols} from {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            ts = _ts(str(row[3]).replace(" ", "T")) if row[3] else 0
            if t0 - 1 <= ts <= t1:
                out[key].append({"purpose": row[0], "latency_ms": row[1], ("cost" if key == "jev" else "tokens"): row[2],
                                 "t": round(ts - t0, 3), "error": row[4]})
    con.close()
    return out


def first(msgs: list[dict], pred, t0: float) -> float | None:
    ts = [_ts(m["ts"]) for m in msgs if pred(m) and _ts(m["ts"]) >= t0 - 0.5]
    return min(ts) - t0 if ts else None


def summarize_feed(run: dict) -> dict[str, Any]:
    msgs, t0 = run["messages"], run["t0"]
    ev = lambda m, t: m["channel"] == "events" and m["type"] == t          # noqa: E731
    misfeed = first(msgs, lambda m: ev(m, "machine_alarm") and (m["data"].get("payload") or {}).get("alarm_code") == "MISFEED", t0)
    inc = first(msgs, lambda m: m["channel"] == "incidents" and m["type"] == "incident_opened", t0)
    notif = first(msgs, lambda m: m["channel"] == "workorders" and m["data"].get("event") == "notification_sent"
                  and m["data"].get("kind", "dispatch") == "dispatch", t0)
    jres = first(msgs, lambda m: m["channel"] == "ai" and m["type"] == "jev_result", t0)
    llm = first(msgs, lambda m: m["channel"] == "ai" and m["type"] == "llm_explanation", t0)
    frames = sorted(({"t": round(_ts(m["ts"]) - t0, 2), "p_jam": m["data"].get("p_jam"), "trigger": m["data"].get("trigger"),
                      "latency_ms": m["data"].get("latency_ms")}
                     for m in msgs if m["channel"] == "ai" and m["type"] == "jev_vision" and _ts(m["ts"]) >= t0),
                    key=lambda x: x["t"])
    incs = [m["data"] for m in msgs if m["channel"] == "incidents" and m["type"] == "incident_opened"]
    seg = lambda a, b: round(b - a, 3) if a is not None and b is not None else None   # noqa: E731
    return {"t_misfeed": misfeed, "t_incident": inc, "t_jev_result": jres, "t_notification": notif, "t_llm": llm,
            "incident_type": (incs[0].get("type") if incs else None),
            "incident_source": ((incs[0].get("payload") or {}).get("facts") or {}).get("source") if incs else None,
            "seg_ramp_to_incident": seg(8.0, inc), "seg_jamvisible_to_incident": seg(10.0, inc),
            "seg_misfeed_to_incident": seg(misfeed, inc), "seg_incident_to_notification": seg(inc, notif),
            "seg_incident_to_jev_result": seg(inc, jres), "seg_notification_to_llm": seg(notif, llm),
            "seg_misfeed_to_llm": seg(misfeed, llm), "jev_frames": frames}


def summarize_ppe(run: dict) -> dict[str, Any]:
    msgs, t0 = run["messages"], run["t0"]
    viol = first(msgs, lambda m: m["channel"] == "events" and m["type"] == "ppe_violation", t0)
    inc = first(msgs, lambda m: m["channel"] == "incidents" and m["type"] == "incident_opened", t0)
    notif = first(msgs, lambda m: m["channel"] == "workorders" and m["data"].get("event") == "notification_sent", t0)
    llm = first(msgs, lambda m: m["channel"] == "ai" and m["type"] == "llm_explanation", t0)
    seg = lambda a, b: round(b - a, 3) if a is not None and b is not None else None   # noqa: E731
    return {"t_violation_event": viol, "t_incident": inc, "t_notification": notif, "t_llm": llm,
            "seg_onset_to_violation": seg(5.0, viol), "seg_violation_to_incident": seg(viol, inc),
            "seg_onset_to_incident": seg(5.0, inc), "seg_incident_to_notification": seg(inc, notif),
            "seg_onset_to_notification": seg(5.0, notif)}


def agg(runs: list[dict], keys: list[str]) -> dict[str, Any]:
    out = {}
    for k in keys:
        vals = [r[k] for r in runs if r.get(k) is not None]
        out[k] = C.lat_stats([v * 1000 for v in vals]) | {"values_s": vals, "n_missing": len(runs) - len(vals)}
        for f in ("mean", "p50", "p95", "max"):
            if out[k].get(f) is not None:
                out[k][f] = round(out[k][f] / 1000, 3)      # 秒
    return out


async def run_variant(variant: str, interval: float, scenarios: list[tuple[str, float]], runs: int, port: int) -> dict:
    cfg = write_config(variant, interval)
    db = Path(yaml.safe_load(cfg.read_text())["paths"]["db"])
    results: dict[str, list] = {}
    with Server(cfg, port) as srv:
        for sc, wait in scenarios:
            for i in range(runs):
                r = await one_run(srv, sc, wait)
                s = summarize_feed(r) if sc == "feed_jam" else summarize_ppe(r)
                s["calls"] = db_calls(db, r["t0"], r["t0"] + wait)
                s["run"] = i
                results.setdefault(sc, []).append(s)
                print(variant, sc, i, {k: v for k, v in s.items() if k.startswith("seg_")}, flush=True)
                (E2E / variant / f"{sc}_{i}.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return results


def cost_summary(runs: list[dict]) -> dict[str, Any]:
    per = []
    for r in runs:
        jev = r["calls"]["jev"]
        llm = r["calls"]["llm"]
        per.append({"jev_calls": len(jev), "jev_cost": round(sum(c.get("cost") or 0 for c in jev), 6),
                    "jev_by_purpose": {p: sum(1 for c in jev if c["purpose"] == p) for p in sorted({c["purpose"] for c in jev})},
                    "llm_calls": len(llm), "llm_tokens": sum(c.get("tokens") or 0 for c in llm),
                    "llm_latency_ms": [c["latency_ms"] for c in llm],
                    "judge_latency_ms": [c["latency_ms"] for c in jev if c["purpose"] == "judge.event"],
                    "vision_latency_ms": [c["latency_ms"] for c in jev if c["purpose"].startswith("vision")]})
    import statistics
    return {"per_run": per,
            "jev_cost_mean": round(statistics.fmean(p["jev_cost"] for p in per), 6) if per else None,
            "jev_calls_mean": round(statistics.fmean(p["jev_calls"] for p in per), 1) if per else None,
            "llm_tokens_mean": round(statistics.fmean(p["llm_tokens"] for p in per), 0) if per else None,
            "judge_latency_ms": C.lat_stats([x for p in per for x in p["judge_latency_ms"] if x]),
            "vision_latency_ms": C.lat_stats([x for p in per for x in p["vision_latency_ms"] if x]),
            "llm_latency_ms": C.lat_stats([x for p in per for x in p["llm_latency_ms"] if x])}


FEED_KEYS = ["seg_ramp_to_incident", "seg_jamvisible_to_incident", "seg_misfeed_to_incident",
             "seg_incident_to_jev_result", "seg_incident_to_notification", "seg_notification_to_llm", "seg_misfeed_to_llm"]
PPE_KEYS = ["seg_onset_to_violation", "seg_violation_to_incident", "seg_onset_to_incident",
            "seg_incident_to_notification", "seg_onset_to_notification"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--port", type=int, default=8010)
    a = ap.parse_args(argv)
    base = asyncio.run(run_variant("interval5", 5.0, [("feed_jam", 45.0), ("ppe_violation", 22.0)], a.runs, a.port))
    fast = asyncio.run(run_variant("interval2", 2.0, [("feed_jam", 45.0)], a.runs, a.port))
    out = {
        "description": "端到端延遲：本機後端（port %d）＋真實 Jev／DeepSeek API；通知器為 console（未接 Discord）" % a.port,
        "runs_per_scenario": a.runs,
        "scenario_times": {"feed_jam": {"ramp_start": 8, "jam_visible": 10, "misfeed_feed_zero": 14},
                           "ppe_violation": {"helmet_removed": 5}},
        "rule": "R-STP-FEEDJAM：送料速率歸零（30 秒內變化）＋ 15 秒內連續 2 張 Jev 關鍵幀 p_jam > 0.7",
        "feed_jam_interval5": {"segments_s": agg(base["feed_jam"], FEED_KEYS), "cost": cost_summary(base["feed_jam"]),
                               "runs": base["feed_jam"]},
        "feed_jam_interval2": {"segments_s": agg(fast["feed_jam"], FEED_KEYS), "cost": cost_summary(fast["feed_jam"]),
                               "runs": fast["feed_jam"]},
        "ppe_violation": {"segments_s": agg(base["ppe_violation"], PPE_KEYS), "cost": cost_summary(base["ppe_violation"]),
                          "runs": base["ppe_violation"]},
    }
    C.save_json("e6_end_to_end.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
