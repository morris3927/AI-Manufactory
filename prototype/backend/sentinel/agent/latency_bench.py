"""維修 Agent 延遲量測（真實 Ollama Cloud LLM ＋ 真實 Jev）。

    python -m sentinel.agent.latency_bench --label after --turns 6 --runs 2

流程：feed_jam 劇本開單 → 接單、許可、LOTO 勾完（IN_REPAIR）→ 連續 N 輪對話，每輪記錄：
- first_text_ms：送出到第一個 agent_delta（WS delta）事件的時間（技術員第一眼看到字）
- reply_ms：送出到 agent_reply（完整文字，安全旗標可能還在檢查）
- flags_ms：送出到 agent_safety_gate／handle() 回傳（含安全旗標；wait=true 的回應時間）
- 每次 LLM 呼叫、每個工具、每次 Jev J4 請求的耗時；工具呼叫輪數
結果寫到 sentinel/eval/agent_latency.json（以 --label 為 key，保留其他 label 的結果）。
資料庫、向量庫都用暫存目錄，不碰正式資料。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..config import BACKEND_DIR, load_settings
from ..db.models import Workorder
from ..dispatch.notifier import ConsoleNotifier
from ..runtime import Runtime
from ..schemas.events import Topic

OUT = BACKEND_DIR / "sentinel" / "eval" / "agent_latency.json"
TURNS = ["LOTO 完成了，下一步要做什麼？", "上下滾輪表面有一條亮帶，接下來呢？", "檢查過滾輪了，沒改善",
         "料捲厚度量起來是 1.52 mm，要怎麼判斷？", "還是一樣卡，下一步？", "現在要怎麼確認修好了？"]


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


class Probe:
    """包住 llm.chat／llm.stream／jev.ask／ToolBox.call 記錄每次耗時（不改程式行為）。"""

    def __init__(self, rt: Runtime):
        self.rows: list[dict] = []
        self.t0 = 0.0
        from . import tools as tools_mod

        def wrap(obj: Any, name: str, kind: str, label=lambda a, k: "") -> None:
            orig = getattr(obj, name)

            async def timed(*a, **k):
                t = time.perf_counter()
                try:
                    return await orig(*a, **k)
                finally:
                    self.rows.append({"kind": kind, "name": label(a, k), "start_ms": round((t - self.t0) * 1000),
                                      "ms": round((time.perf_counter() - t) * 1000)})
            setattr(obj, name, timed)

        wrap(rt.llm, "chat", "llm", lambda a, k: k.get("purpose", ""))
        if hasattr(rt.llm, "stream_chat"):
            orig_stream = rt.llm.stream_chat
            probe = self

            def stream_chat(*a, **k):
                t = time.perf_counter()
                agen = orig_stream(*a, **k)

                async def gen():
                    first = None
                    try:
                        async for item in agen:
                            if first is None:
                                first = time.perf_counter()
                            yield item
                    finally:
                        probe.rows.append({"kind": "llm", "name": k.get("purpose", "") + "(stream)",
                                           "start_ms": round((t - probe.t0) * 1000),
                                           "ttft_ms": round(((first or time.perf_counter()) - t) * 1000),
                                           "ms": round((time.perf_counter() - t) * 1000)})
                return gen()
            rt.llm.stream_chat = stream_chat
        wrap(rt.jev, "ask", "jev", lambda a, k: k.get("purpose", ""))
        wrap(tools_mod.ToolBox, "call", "tool", lambda a, k: a[1] if len(a) > 1 else "")

    def take(self) -> list[dict]:
        rows, self.rows = self.rows, []
        return rows


async def run_once(rt: Runtime, probe: Probe, turns: list[str]) -> list[dict]:
    await rt.engine.run_stepped(rt.scenarios["feed_jam"], duration_s=16)
    wo = rt.db.all(select(Workorder))[-1]
    await rt.workorders.ack(wo.id, "P-STP-TC1")
    await rt.workorders.permit(wo.id, "P-STP-TC1", "request")
    await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")
    loto = rt.agent.ctx.loto_for(wo.id)
    for s in loto.steps:
        await rt.workorders.check_loto(wo.id, s.no, "P-STP-TC1", loto_id=loto.loto_id, steps=[x.no for x in loto.steps])
    assert rt.workorders.get(wo.id).status == "IN_REPAIR"
    q = rt.bus.subscribe(Topic.AI, "latency-bench", maxsize=10000)
    out = []
    for msg in turns:
        while not q.empty():
            q.get_nowait()
        probe.take()
        marks: dict[str, float] = {}
        t0 = probe.t0 = time.perf_counter()

        async def watch():
            while True:
                evt = await q.get()
                if evt.payload.get("workorder_id") != wo.id:
                    continue
                key = {"agent_delta": "first_text", "agent_reply": "reply", "agent_safety_gate": "flags"}.get(evt.type)
                if key and key not in marks:
                    marks[key] = time.perf_counter()
        w = asyncio.create_task(watch())
        reply = await rt.agent.handle(wo.id, msg, "P-STP-TC1")
        t_done = time.perf_counter()
        await asyncio.sleep(0.05)
        w.cancel()
        rows = probe.take()
        reply_t = marks.get("reply", t_done)
        first = min(marks.get("first_text", reply_t), reply_t)      # 舊版：delta 跟完整回覆同時送出
        llm = [r for r in rows if r["kind"] == "llm"]
        gate = [r for r in rows if r["kind"] == "jev" and r["name"].startswith("gate")]
        tools = [r for r in rows if r["kind"] == "tool"]
        gate_ms = (max(r["start_ms"] + r["ms"] for r in gate) - min(r["start_ms"] for r in gate)) if gate else 0
        out.append({"message": msg, "first_text_ms": round((first - t0) * 1000), "reply_ms": round((reply_t - t0) * 1000),
                    "flags_ms": round((t_done - t0) * 1000), "llm_calls": len(llm), "llm": llm,
                    "tool_rounds": max(0, len([r for r in llm if r["name"].startswith("agent")]) - 1),
                    "tools": tools, "gate_requests": len(gate), "gate_wall_ms": gate_ms,
                    "steps": len(reply.steps), "flagged": sum(s.flagged for s in reply.steps),
                    "gate_sources": sorted({s.source for s in reply.steps}), "hypothesis": reply.hypothesis,
                    "llm_used": reply.llm_used, "timings": getattr(reply, "timings", None),
                    "content": reply.content})
        print(f"[{msg[:14]}] first={out[-1]['first_text_ms']} reply={out[-1]['reply_ms']} "
              f"flags={out[-1]['flags_ms']} llm={[r['ms'] for r in llm]} gate={gate_ms}ms/{len(gate)}req "
              f"steps={len(reply.steps)} src={out[-1]['gate_sources']}", flush=True)
    rt.bus.unsubscribe(q)
    return out


async def one_runtime(turns: int) -> tuple[list[dict], Any]:
    tmp = Path(tempfile.mkdtemp(prefix="sentinel-bench-"))
    s = load_settings(BACKEND_DIR / "config.yaml",
                      paths={"db": str(tmp / "s.db"), "chroma": str(tmp / "chroma"), "drafts": str(tmp / "drafts"),
                             "keyframes": str(tmp / "kf")},
                      kb={"embedding": "hash"}, app={"ambient": False},
                      vision={"yolo_weights": "models/_none_for_bench.pt"})
    from ..clock import ManualClock
    rt = Runtime(s, clock=ManualClock(s.app.sim_start), notifier=ConsoleNotifier())
    from ..kb.ingest import ingest
    if rt.kb.store is not None:
        ingest(s.path("knowledge"), rt.kb.store)
    await rt.start(realtime=False, warmup=True)
    if hasattr(rt.llm, "warmup"):
        await rt.llm.warmup()
    probe = Probe(rt)
    try:
        return await run_once(rt, probe, TURNS[:turns]), s
    finally:
        await rt.stop()


async def main(label: str, turns: int, runs: int) -> dict:
    rows: list[dict] = []
    for _ in range(runs):
        r, s = await one_runtime(turns)
        rows += r
    summ = {k: {"p50": pct([r[k] for r in rows], 0.5), "p95": pct([r[k] for r in rows], 0.95),
                "mean": round(statistics.mean(r[k] for r in rows), 1)}
            for k in ("first_text_ms", "reply_ms", "flags_ms", "gate_wall_ms", "llm_calls", "tool_rounds", "gate_requests")}
    result = {"model": s.llm.model, "jev_model": s.jev.model, "turns": len(rows), "summary": summ, "rows": rows}
    data = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    data[label] = result
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({label: summ}, ensure_ascii=False, indent=1))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="after")
    ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.label, a.turns, a.runs))
