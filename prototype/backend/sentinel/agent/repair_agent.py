"""維修 Agent（AG-1、AG-2、AG-5、AG-6；J4 安全把關）。一張工單一個 session，對話存在 agent_message。

程式強制的規則（不靠提示詞）：
1. 作業許可先行：PERMIT_APPROVED 前只顯示作業許可與 LOTO 預覽（要隔離哪些能量源）。
2. LOTO 先行：LOTO 勾完（IN_REPAIR）前只顯示 LOTO 檢查表與準備／隔離段落（固定模板，不呼叫 LLM）；
   get_sop(phase=inspect|repair) 等工具在 IN_REPAIR 之前不開放。
3. 安全把關：回覆中的每個步驟送 jev.gate.step；超過門檻（config thresholds.gate）→ 標記，再分兩級：
   supervisor（標紅、自動 request_supervisor、等主管確認）／caution（黃底提醒，不需任何人確認，只記稽核）。
4. 不能叫人復電：輸出過濾「可以復電」「解除上鎖」等指示。
5. 不能改變工單狀態：沒有這類工具，WorkorderService 也會拒絕 agent 身分的轉換。

延遲（Demo Day 目標：首字 < 3 s、含安全旗標 ≤ 6 s；量測 python -m sentinel.agent.latency_bench）：
- 程式先把工單、根因前三名、目前狀態的 SOP 段落、LOTO 進度、訊號摘要、相似案例放進提示詞 → 預設不開工具輪
  （agent_tool_rounds=0；開了也有上限）。
- LLM stream=true、think=false；每一行先過復電過濾才以 agent_delta 推到 WS（未過濾的字不會出現在畫面）。
- 每個步驟一寫完就立刻送 J4（平行），文字先送 agent_reply，安全旗標隨後以 agent_safety_gate 補上；
  handle() 仍等全部步驟把關完才回傳（wait=true 的回應含旗標）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import col, select

from ..audit.log import AuditLogger
from ..bus import EventBus
from ..clock import Clock
from ..config import Thresholds
from ..db.models import AgentMessage
from ..db.session import Database
from ..dispatch.service import DispatchService
from ..dispatch.workorder import WorkorderService
from ..jev import gate
from ..jev.client import JevClient, JevUnavailable
from ..kb.retrieve import KnowledgeBase, LotoChecklist
from ..llm.client import LlmClient, LlmResult, LlmUnavailable
from ..rules.windows import Windows
from ..schemas.events import Event, Topic
from ..schemas.plant import Plant
from . import guards
from .tools import ToolBox, schemas_for, tool_result_message

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "repair_system.md").read_text(encoding="utf-8")
REJECT_PAT = re.compile(r"(無效|沒效|沒改善|未改善|還是一樣|仍然|一樣卡|不行|沒用|沒有改善)")
MAX_TOOL_ROUNDS = 2          # 工具輪上限（只有 agent_tool_rounds > 0 時才會用到）
GATE_MARK = "safety_gate"    # tool_calls 內記錄把關進度的項目（message_dict 轉成 gate 欄位，不當工具顯示）


@dataclass
class AgentContext:
    db: Database
    bus: EventBus
    clock: Clock
    audit: AuditLogger
    kb: KnowledgeBase
    llm: LlmClient
    jev: JevClient
    windows: Windows
    plant: Plant
    workorders: WorkorderService
    dispatch: DispatchService
    thresholds: Thresholds = field(default_factory=Thresholds)
    require_permit: bool = True
    agent_tool_rounds: int = 0                  # 0：資料已預先放進提示詞，不給 LLM 工具（最快）
    llm_think: bool | None = False              # DeepSeek V4.1 Flash 的思考模式：關掉後首字由約 3 s 降到約 0.6 s
    llm_options: dict = field(default_factory=lambda: {"num_predict": 700})

    def loto_for(self, wo_id: str) -> LotoChecklist:
        wo = self.workorders.get(wo_id)
        inc = self.workorders.incident(wo)
        m = self.plant.machines.get(inc.machine_id or "")
        diag = self.dispatch.diagnosis_for(inc.id)
        for sid in (diag.sop_ids if diag else []):
            lid = ((self.kb.sops.get(sid) or {}).get("safety") or {}).get("loto_checklist_id")
            if lid:
                return self.kb.loto_for(m.type if m else None, loto_id=lid)
        return self.kb.loto_for(m.type if m else None)


class StepCheck(BaseModel):
    text: str
    flagged: bool
    needs_loto: float = 0
    enters_danger_zone: float = 0
    high_voltage: float = 0
    energized: float = 0
    reasons: list[str] = Field(default_factory=list)
    level: str = "none"                # none／lockout／caution／supervisor（gate.gate_level）
    needs_lockout: bool = False        # 上鎖掛牌還沒完成，而且這一步要碰機台
    requires_supervisor: bool = False  # = level == "supervisor"
    source: str = "jev"


class AgentReply(BaseModel):
    workorder_id: str
    phase: str                         # loto／repair／safety_only
    content: str
    steps: list[StepCheck] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    supervisor_requested: bool = False
    restore_filtered: list[str] = Field(default_factory=list)
    energized_rewritten: list[str] = Field(default_factory=list)
    message_id: str | None = None
    user_message_id: str | None = None
    hypothesis: str | None = None
    llm_used: bool = False
    timings: dict[str, Any] = Field(default_factory=dict)   # 延遲分解（ms）：prefetch／ttft／llm／gate_tail／total


class RepairAgent:
    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    # ── 對外 ───────────────────────────────────────────────────
    async def handle(self, workorder_id: str, user_message: str, person_id: str) -> AgentReply:
        ctx = self.ctx
        t0 = time.perf_counter()
        wo = ctx.workorders.get(workorder_id)
        inc = ctx.workorders.incident(wo)
        user_row = self._save(workorder_id, "user", user_message)
        await self._publish(inc, workorder_id, "agent_message", {"message": message_dict(user_row)})
        tools = ToolBox(ctx, workorder_id)
        loto_id = ctx.loto_for(workorder_id).loto_id if wo.category != "safety" else "LOTO"
        turn = _Turn(self, inc, tools, loto_id, t0)

        if wo.category == "safety":
            reply = AgentReply(workorder_id=workorder_id, phase="safety_only", content=(
                "這是工安類事件：請現場主管到場確認人員狀況並提醒配戴 PPE。"
                "依規定，工安事件由現場主管處理，維修助理不提供排除方法。"))
        else:
            reply = await self._repair_reply(tools, inc, user_message, turn)

        reply.timings = {**turn.timings, **reply.timings}
        # 4. 不能叫人復電（所有回覆都要過濾；串流時每行已先過濾，存檔內容以全文過濾為準）
        #    上鎖完成後的維修回覆，結尾一律附上復機步驟（由當初上鎖的人做）；上鎖前還談不上復機
        repair_mode = wo.category != "safety" and not guards.restricted(wo.status, ctx.require_permit)
        fr = guards.filter_restore(reply.content, loto_id, remind=repair_mode)
        reply.content = fr.text
        reply.restore_filtered, reply.energized_rewritten = fr.removed, fr.rewritten
        if fr.removed:
            reply.safety_flags.append("restore_power_filtered")
        if fr.rewritten:
            reply.safety_flags.append("energized_step_rewritten")
        tool_calls = [{"name": "output_filter", "args": {"restore_filtered": fr.removed,
                                                          "energized_rewritten": fr.rewritten}}] \
            if (fr.removed or fr.rewritten) else []
        gated = reply.phase == "repair"          # 3. 安全把關（LOTO 清單本身就是隔離程序，不需要再送 J4）
        steps = guards.extract_steps(reply.content) if gated else []
        if gated:
            for st in steps:                     # 串流時還沒開始把關的步驟（例如 LLM 失敗改用 SOP 原文）現在補上
                turn.start_gate(st)
        row = turn.finalize(reply.content, tool_calls, steps)
        reply.timings["reply_ms"] = turn.ms()
        msg = message_dict(row) | ({"safety_flags": turn.partial_flags} if steps else {})
        await self._publish(inc, workorder_id, "agent_reply", {"message": msg, "flags": msg["safety_flags"],
                                                               "gate_pending": bool(steps),
                                                               "reply": reply.model_dump()})
        if gated:
            tg = time.perf_counter()
            await self._gate(reply, tools, turn, steps)
            reply.timings["gate_tail_ms"] = round((time.perf_counter() - tg) * 1000)
        row = turn.row
        reply.message_id = str(row.id)
        reply.user_message_id = str(user_row.id)
        reply.timings["total_ms"] = turn.ms()
        reply.timings["gate_requests"] = turn.gate_requests
        ctx.audit.record("agent_reply", actor=f"agent:{workorder_id}", trace_id=inc.trace_id, phase=reply.phase,
                         safety_flags=reply.safety_flags, supervisor_requested=reply.supervisor_requested,
                         llm_used=reply.llm_used, message_id=row.id, timings=reply.timings)
        return reply

    def history(self, workorder_id: str) -> list[AgentMessage]:
        return self.ctx.db.all(select(AgentMessage).where(AgentMessage.workorder_id == workorder_id)
                               .order_by(col(AgentMessage.id)))

    def _rejected(self, wo_id: str) -> list[str]:
        rows = self.ctx.db.all(select(AgentMessage).where(AgentMessage.workorder_id == wo_id,
                                                          AgentMessage.role == "tool"))
        return [r.content.removeprefix("[排除] ").strip() for r in rows if r.content.startswith("[排除]")]

    async def _repair_reply(self, tools: ToolBox, inc: Any, user_message: str, turn: "_Turn") -> AgentReply:
        ctx = self.ctx
        tp = time.perf_counter()
        info = await tools.call("get_workorder")
        top3 = info["root_cause_top3"]
        rejected = self._rejected(tools.wo_id)
        had_reply = any(m.role == "assistant" for m in self.history(tools.wo_id)[-6:])
        current = next((r for r in top3 if r["id"] not in rejected), None)
        if current and had_reply and REJECT_PAT.search(user_message):
            ctx.db.add(AgentMessage(workorder_id=tools.wo_id, role="tool", content=f"[排除] {current['id']}",
                                    tool_calls=[{"name": "record_step", "args": {"step": current["id"], "result": "無效"}}],
                                    ts=ctx.clock.now()))
            rejected.append(current["id"])
            current = next((r for r in top3 if r["id"] not in rejected), None)

        # 預先取好的資料（程式決定查什麼）：LLM 通常不需要再呼叫工具
        sop_chunks: list[dict] = []
        if current:
            for phase in ("inspect", "repair"):
                sop_chunks += await tools.call("get_sop", {"failure_mode_id": current["id"], "phase": phase})
        cases = await tools.call("search_cases", {"query": f"{inc.type} {current['id'] if current else ''}"})
        signals = [await tools.call("get_machine_signals", {"signal": s, "last_min": 10})
                   for s in ("feed_rate", "feed_length_error_mm", "motor_current_a")] if inc.machine_id else []
        loto = await tools.call("get_loto")
        context = {"workorder": info, "current_hypothesis": current, "rejected_hypotheses": rejected,
                   "lockout_done": not guards.restricted(tools.status()),
                   "loto": {"loto_id": loto["loto_id"], "all_checked": all(s["checked"] for s in loto["steps"]),
                            "energy_sources": [e.get("id") for e in loto.get("energy_sources") or []]},
                   "sop": [{"sop_id": c["sop_id"], "phase": c["phase"], "text": c["text"]} for c in sop_chunks],
                   "similar_cases": [c["short"] for c in cases], "signals": signals}
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "system", "content": "工單資料（JSON，已是最新，直接依此回答）：\n"
                                 + json.dumps(context, ensure_ascii=False)}]
        for m in self.history(tools.wo_id)[-9:-1]:
            if m.role in ("user", "assistant") and m.content:
                messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": user_message})
        turn.timings["prefetch_ms"] = round((time.perf_counter() - tp) * 1000)

        try:
            content = await self._llm_loop(messages, tools, inc.trace_id, turn)
            used = True
        except LlmUnavailable:
            used = False
            if sop_chunks:
                content = "（AI 說明暫不可用，以下為 SOP 原文）\n" + "\n".join(c["text"] for c in sop_chunks)
            else:
                content = "（AI 說明暫不可用，且沒有對應 SOP）請聯絡工程師確認。"
        if current is None and top3:
            content = "前三名根因都已排除，已通知主管。\n" + content
            await tools.call("request_supervisor", {"reason": "根因前三名都已排除，需要工程師支援"})
        return AgentReply(workorder_id=tools.wo_id, phase="repair", content=content,
                          hypothesis=current["id"] if current else None, llm_used=used, timings=turn.timings)

    async def _llm_loop(self, messages: list[dict], tools: ToolBox, trace_id: str, turn: "_Turn") -> str:
        """串流呼叫 LLM。工具輪預設關閉（資料已預先放進提示詞）；開啟時最多 min(agent_tool_rounds, MAX_TOOL_ROUNDS) 輪。"""
        ctx = self.ctx
        allowed = guards.allowed_tools(tools.status(), ctx.require_permit) - {"request_supervisor"}
        rounds = min(ctx.agent_tool_rounds, MAX_TOOL_ROUNDS)
        turn.timings.update(llm_calls=0, tool_rounds=0, llm_ms=[], ttft_ms=None)
        for i in range(rounds + 1):
            offer = schemas_for(allowed) if i < rounds else None
            sf = guards.StreamFilter(turn.loto_id, remind=not guards.restricted(tools.status()))   # 上鎖後才附復機
            res: LlmResult | None = None
            async for item in ctx.llm.stream_chat(messages, tools=offer, purpose="agent.reply", trace_id=trace_id,
                                                  options=ctx.llm_options, think=ctx.llm_think):
                if isinstance(item, LlmResult):
                    res = item
                elif lines := sf.feed(item):
                    await turn.emit(lines)
            assert res is not None
            turn.timings["llm_calls"] += 1
            turn.timings["llm_ms"].append(round(res.latency_ms or 0))
            if turn.timings["ttft_ms"] is None:
                turn.timings["ttft_ms"] = res.ttft_ms
            if not (offer and res.tool_calls):
                if lines := sf.finish():
                    await turn.emit(lines)
                return res.content.strip()
            turn.timings["tool_rounds"] += 1
            turn.discard_stream()                      # 這一輪只是在叫工具：已顯示的前言不算最終回覆
            messages.append({"role": "assistant", "content": res.content or "", "tool_calls": res.tool_calls})
            for tc in res.tool_calls:
                fn = tc.get("function") or {}
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                try:
                    result = await tools.call(name, args) if name in allowed else {"error": f"工具 {name} 不開放"}
                except guards.ToolDenied as e:
                    result = {"error": str(e)}
                except TypeError as e:
                    result = {"error": f"參數錯誤：{e}"}
                messages.append(tool_result_message(name, result))
        raise AssertionError("unreachable")

    # ── 3. J4 安全把關 ─────────────────────────────────────────
    async def check_step(self, text: str, tools: ToolBox, inc: Any) -> StepCheck:
        """單一步驟的 J4（門檻來自 config thresholds.gate）；Jev 不可用 → 關鍵字保守退路。"""
        ctx = self.ctx
        m = ctx.plant.machines.get(inc.machine_id or "")
        th, sup = ctx.thresholds.gate, ctx.thresholds.gate_supervisor
        loto_done = not guards.restricted(tools.status())          # ≥ LOTO_DONE：needs_loto 已滿足
        if guards.observation_only(text):                           # 只看面板／畫面／紀錄：不碰機台，不送 J4
            return StepCheck(text=text, flagged=False, source="observation")
        if guards.ENERGIZED_MARK in text:                          # 測試／定位改寫過的步驟：一定要主管在場
            return StepCheck(text=text, flagged=True, energized=1.0, reasons=["energized_test"], level="supervisor",
                             requires_supervisor=True, needs_lockout=not loto_done, source="energized_test")
        try:
            g = await gate.step(ctx.jev, text, m.type if m else None, thresholds=th, supervisor_thresholds=sup,
                                loto_done=loto_done, trace_id=inc.trace_id)
            return StepCheck(text=text, flagged=g.flagged, needs_loto=g.needs_loto,
                             enters_danger_zone=g.enters_danger_zone, high_voltage=g.high_voltage,
                             energized=g.energized, reasons=g.reasons, level=g.level,
                             needs_lockout=g.needs_lockout, requires_supervisor=g.level == "supervisor")
        except JevUnavailable:                                     # 關鍵字退路分不出輕重 → 一律需主管確認
            p = guards.keyword_gate(text)
            reasons = gate.gate_reasons(p, th, loto_done)
            level = gate.gate_level(p, reasons, sup, source="keyword")
            return StepCheck(text=text, flagged=bool(reasons), reasons=reasons, level=level,
                             needs_lockout="needs_loto" in reasons, requires_supervisor=level == "supervisor",
                             source="keyword", **p)

    async def _gate(self, reply: AgentReply, tools: ToolBox, turn: "_Turn", steps: list[str]) -> None:
        reply.steps = [await turn.gate_result(st) for st in steps]
        sup = [s for s in reply.steps if s.flagged and s.level == "supervisor"]
        caution = [s for s in reply.steps if s.flagged and s.level == "caution"]
        lockout = [s for s in reply.steps if s.flagged and s.needs_lockout]
        if sup or caution or lockout:
            reply.safety_flags.append("j4_flagged")
        if lockout:                              # 上鎖前要碰機台的步驟：提醒先上鎖，不找主管
            reply.safety_flags.append("j4_lockout")
        if caution:                              # 注意級：不找主管，只留稽核
            reply.safety_flags.append("j4_caution")
            ctx = self.ctx
            ctx.audit.record("safety_gate_caution", actor=f"agent:{tools.wo_id}", trace_id=turn.inc.trace_id,
                             workorder_id=tools.wo_id,
                             steps=[{"step": s.text, "reasons": s.reasons,
                                     "probs": {k: getattr(s, k) for k in s.reasons if hasattr(s, k)}} for s in caution])
        if sup:
            reply.safety_flags.append("j4_supervisor")
            reply.supervisor_requested = True
            reasons = "；".join(f"「{s.text[:40]}」" for s in sup[:3])
            await tools.call("request_supervisor", {"reason": f"維修步驟涉及危險操作，請主管確認：{reasons}"})
        await turn.gate_done(reply.steps)

    def _save(self, wo_id: str, role: str, content: str, *, safety_flags: list | None = None,
              tool_calls: list | None = None) -> AgentMessage:
        row = AgentMessage(workorder_id=wo_id, role=role, content=content, tool_calls=tool_calls or [],
                           safety_flags=safety_flags or [], ts=self.ctx.clock.now())
        self.ctx.db.add(row)
        return row

    async def _publish(self, inc: Any, wo_id: str, type_: str, payload: dict) -> None:
        await self.ctx.bus.publish(Event(ts=self.ctx.clock.now(), topic=Topic.AI, source="agent", line=inc.line,
                                         machine_id=inc.machine_id, camera_id=inc.camera_id, type=type_,
                                         trace_id=inc.trace_id, payload={"workorder_id": wo_id, **payload}))


def flag_dicts(steps: list[StepCheck]) -> list[dict]:
    flags = []
    for i, st in enumerate(steps):
        if not st.flagged:
            continue
        probs = {"needs_loto": st.needs_loto, "enters_danger_zone": st.enters_danger_zone,
                 "high_voltage": st.high_voltage, "energized": st.energized}
        level = st.level if st.level in ("lockout", "caution", "supervisor") else "supervisor"   # 被標記卻沒等級 → 保守
        sup = level == "supervisor"
        flags.append({"step_index": i, "step_text": st.text, "probs": probs,
                      "max_p": max(probs[k] for k in st.reasons if k in probs) if any(k in probs for k in st.reasons)
                      else 1.0,
                      "reasons": st.reasons, "level": level, "needs_lockout": st.needs_lockout,
                      "requires_supervisor": sup, "supervisor_requested": sup,
                      "source": st.source})
    return flags


class _Turn:
    """一輪回覆的串流狀態：assistant 訊息列（第一行出現時建立）、已放出的文字、每個步驟的 J4 task。"""

    def __init__(self, agent: RepairAgent, inc: Any, tools: ToolBox, loto_id: str, t0: float):
        self.agent, self.inc, self.tools, self.loto_id, self.t0 = agent, inc, tools, loto_id, t0
        self.row: AgentMessage | None = None
        self.lines: list[str] = []
        self.tasks: dict[str, asyncio.Task[StepCheck]] = {}
        self.checked: list[str] = []
        self.lock = asyncio.Lock()
        self.timings: dict[str, Any] = {}
        self.gate_requests = 0
        self.partial_flags: list[dict] = []
        self.closed = False

    def ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)

    def _ensure_row(self) -> AgentMessage:
        if self.row is None:
            self.row = self.agent._save(self.tools.wo_id, "assistant", "",
                                        tool_calls=[{"name": GATE_MARK, "args": {"status": "pending", "checked": []}}])
        return self.row

    def _update(self, **fields: Any) -> None:
        db = self.agent.ctx.db
        with db.session() as s:
            m = s.get(AgentMessage, self.row.id)          # type: ignore[union-attr]
            for k, v in fields.items():
                setattr(m, k, v)
            s.add(m)
            s.commit()
            self.row = m

    def _gate_mark(self, status: str) -> list[dict]:
        rest = [t for t in (self.row.tool_calls or []) if t.get("name") != GATE_MARK]  # type: ignore[union-attr]
        return rest + [{"name": GATE_MARK, "args": {"status": status, "checked": list(self.checked)}}]

    async def emit(self, lines: list[str]) -> None:
        """放出已過濾的完整行：WS delta（累積全文），步驟行立刻開始 J4。"""
        row = self._ensure_row()
        if "first_text_ms" not in self.timings:
            self.timings["first_text_ms"] = self.ms()
        self.lines += lines
        for line in lines:
            if (st := guards.step_text(line)) is not None:
                self.start_gate(st)
        await self.agent._publish(self.inc, self.tools.wo_id, "agent_delta",
                                  {"message_id": str(row.id), "text": "\n".join(self.lines), "chunk": "\n".join(lines)})

    def discard_stream(self) -> None:
        self.lines = []

    def start_gate(self, text: str) -> None:
        if text in self.tasks:
            return
        self.gate_requests += guards.ENERGIZED_MARK not in text
        task = asyncio.create_task(self.agent.check_step(text, self.tools, self.inc), name=f"gate:{text[:20]}")
        task.add_done_callback(lambda t, text=text: asyncio.ensure_future(self._partial(text, t)))
        self.tasks[text] = task

    async def gate_result(self, text: str) -> StepCheck:
        self.start_gate(text)
        return await self.tasks[text]

    async def _partial(self, text: str, task: asyncio.Task) -> None:
        """單一步驟把關完成：更新訊息列並推送 safety_gate（done=false），前端把該步驟的「檢查中」換成結果。"""
        if task.cancelled() or task.exception() is not None or self.row is None or self.closed:
            return
        async with self.lock:
            if self.closed:
                return
            self.checked.append(text)
            done = [self.tasks[t].result() for t in self.tasks if self.tasks[t].done() and not self.tasks[t].exception()]
            flags = self.partial_flags = [f | {"step_index": -1} for f in flag_dicts(done)]   # 最終索引在 gate_done 決定
            self._update(tool_calls=self._gate_mark("pending"))
            await self.agent._publish(self.inc, self.tools.wo_id, "agent_safety_gate",
                                      {"message_id": str(self.row.id), "flags": flags, "done": False,
                                       "gate": {"status": "pending", "checked": list(self.checked)}})

    def finalize(self, content: str, tool_calls: list[dict], steps: list[str]) -> AgentMessage:
        """存最終（全文過濾後）內容；把關還在跑時 gate.status=pending。"""
        status = "pending" if steps else "done"
        if self.row is None:
            self.row = self.agent._save(self.tools.wo_id, "assistant", content,
                                        tool_calls=tool_calls + ([{"name": GATE_MARK, "args": {"status": status,
                                                                                                 "checked": []}}]
                                                                 if steps else []))
            return self.row
        self.row.tool_calls = tool_calls + [t for t in self.row.tool_calls or [] if t.get("name") == GATE_MARK]
        self._update(content=content, tool_calls=self._gate_mark(status) if steps else tool_calls)
        return self.row

    async def gate_done(self, steps: list[StepCheck]) -> None:
        async with self.lock:
            self.closed = True
            self.checked = [s.text for s in steps]
            flags = flag_dicts(steps)
            self._update(safety_flags=flags, tool_calls=self._gate_mark("done"))
            await self.agent._publish(self.inc, self.tools.wo_id, "agent_safety_gate",
                                      {"message_id": str(self.row.id), "flags": flags, "done": True,  # type: ignore[union-attr]
                                       "gate": {"status": "done", "checked": list(self.checked)}})


def message_dict(m: AgentMessage) -> dict:
    """前端 AgentMessage 格式。gate：J4 把關進度（pending 時前端在步驟旁顯示「安全把關檢查中…」）。"""
    calls = m.tool_calls or []
    mark = next((t.get("args") or {} for t in calls if t.get("name") == GATE_MARK), None)
    return {"id": str(m.id), "workorder_id": m.workorder_id, "role": m.role, "content": m.content,
            "steps": guards.extract_steps(m.content) if m.role == "assistant" else [],
            "tool_calls": [t for t in calls if t.get("name") != GATE_MARK], "safety_flags": m.safety_flags or [],
            "gate": {"status": mark.get("status", "done"), "checked": mark.get("checked", [])} if mark else None,
            "ts": m.ts.isoformat()}
