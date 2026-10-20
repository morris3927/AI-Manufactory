"""維修 Agent（AG-1、AG-2、AG-5、AG-6；J4 安全把關）。一張工單一個 session，對話存在 agent_message。

程式強制的規則（不靠提示詞）：
1. LOTO 先行：工單還沒到 LOTO_DONE 前，回覆只會是 LOTO 清單與準備步驟（固定模板，不呼叫 LLM），
   get_sop(phase=inspect|repair) 等工具不開放。
2. 作業許可（M2）：require_permit=True 時，PERMIT 核准（IN_REPAIR）前同上。
3. 安全把關：回覆中的每個步驟送 jev.gate.step；任一機率 > 0.3 → 標紅並自動 request_supervisor。
4. 不能叫人復電：輸出過濾「可以復電」「解除上鎖」等指示。
5. 不能改變工單狀態：沒有這類工具，WorkorderService 也會拒絕 agent 身分的轉換。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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
from ..llm.client import LlmClient, LlmUnavailable
from ..rules.windows import Windows
from ..schemas.events import Event, Topic
from ..schemas.plant import Plant
from . import guards
from .tools import ToolBox, schemas_for, tool_result_message

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "repair_system.md").read_text(encoding="utf-8")
REJECT_PAT = re.compile(r"(無效|沒效|沒改善|未改善|還是一樣|仍然|一樣卡|不行|沒用|沒有改善)")
MAX_TOOL_ROUNDS = 3


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
    require_permit: bool = False

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
    source: str = "jev"


class AgentReply(BaseModel):
    workorder_id: str
    phase: str                         # loto／repair／safety_only
    content: str
    steps: list[StepCheck] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    supervisor_requested: bool = False
    restore_filtered: list[str] = Field(default_factory=list)
    message_id: str | None = None
    user_message_id: str | None = None
    hypothesis: str | None = None
    llm_used: bool = False


class RepairAgent:
    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    # ── 對外 ───────────────────────────────────────────────────
    async def handle(self, workorder_id: str, user_message: str, person_id: str) -> AgentReply:
        ctx = self.ctx
        wo = ctx.workorders.get(workorder_id)
        inc = ctx.workorders.incident(wo)
        user_row = self._save(workorder_id, "user", user_message)
        await self._publish(inc, workorder_id, "agent_message", {"message": message_dict(user_row)})
        tools = ToolBox(ctx, workorder_id)

        if wo.category == "safety":
            reply = AgentReply(workorder_id=workorder_id, phase="safety_only", content=(
                "這是工安類事件：請班長到場確認人員狀況並提醒配戴 PPE。"
                "依規定，工安事件由現場主管處理，維修助理不提供排除方法。"))
        elif guards.restricted(wo.status, ctx.require_permit):
            reply = await self._loto_reply(tools, wo.status)
        else:
            reply = await self._repair_reply(tools, inc, user_message)

        # 4. 不能叫人復電（所有回覆都要過濾）
        reply.content, removed = guards.filter_restore(reply.content)
        reply.restore_filtered = removed
        if removed:
            reply.safety_flags.append("restore_power_filtered")
        # 3. 安全把關（LOTO 清單本身就是隔離程序，不需要再送 J4）
        if reply.phase == "repair":
            await self._gate(reply, tools, inc)

        flags = [{"step_index": i, "step_text": st.text,
                  "probs": {"needs_loto": st.needs_loto, "enters_danger_zone": st.enters_danger_zone,
                            "high_voltage": st.high_voltage},
                  "max_p": max(st.needs_loto, st.enters_danger_zone, st.high_voltage),
                  "supervisor_requested": True, "source": st.source}
                 for i, st in enumerate(reply.steps) if st.flagged]
        tool_calls = [{"name": "output_filter", "args": {"restore_filtered": removed}}] if removed else []
        row = self._save(workorder_id, "assistant", reply.content, safety_flags=flags, tool_calls=tool_calls)
        reply.message_id = str(row.id)
        reply.user_message_id = str(user_row.id)
        ctx.audit.record("agent_reply", actor=f"agent:{workorder_id}", trace_id=inc.trace_id, phase=reply.phase,
                         safety_flags=reply.safety_flags, supervisor_requested=reply.supervisor_requested,
                         llm_used=reply.llm_used, message_id=row.id)
        await self._publish(inc, workorder_id, "agent_reply", {"message": message_dict(row), "flags": flags,
                                                               "reply": reply.model_dump()})
        return reply

    def history(self, workorder_id: str) -> list[AgentMessage]:
        return self.ctx.db.all(select(AgentMessage).where(AgentMessage.workorder_id == workorder_id)
                               .order_by(col(AgentMessage.id)))

    # ── 1. LOTO 先行（固定模板，不呼叫 LLM）─────────────────────
    async def _loto_reply(self, tools: ToolBox, status: str) -> AgentReply:
        loto = await tools.call("get_loto")
        wo_info = await tools.call("get_workorder")
        lines = ["依職業安全衛生設施規則第 57 條，開始檢查或維修前必須先完成上鎖掛牌（LOTO）。"]
        if status in ("OPEN", "ASSIGNED", "ESCALATED"):
            lines.append("請先在工單按「接單」。")
        lines.append(f"LOTO 檢查表 {loto['loto_id']}（逐項勾選）：")
        for s in loto["steps"]:
            mark = "☑" if s["checked"] else "☐"
            lines.append(f"{mark} {s['no']}. {s['action']}" + (f"（確認：{s['check']}）" if s.get("check") else ""))
        prep: list[str] = []
        for fid in [r["id"] for r in wo_info["root_cause_top3"][:1]]:
            for phase in ("prepare", "isolate"):
                for chunk in await tools.call("get_sop", {"failure_mode_id": fid, "phase": phase}):
                    prep.append(chunk["text"])
        if prep:
            lines.append("SOP 準備與隔離段落：")
            lines.extend(prep)
        lines.append("全部勾選完成（LOTO_DONE）後，我才會提供檢查與維修步驟。")
        if self.ctx.require_permit:
            lines.append("LOTO 完成後還需要班長核准作業許可。")
        return AgentReply(workorder_id=tools.wo_id, phase="loto", content="\n".join(lines))

    # ── 5. 動態排查（程式決定查什麼，LLM 只負責寫回覆）─────────
    def _rejected(self, wo_id: str) -> list[str]:
        rows = self.ctx.db.all(select(AgentMessage).where(AgentMessage.workorder_id == wo_id,
                                                          AgentMessage.role == "tool"))
        return [r.content.removeprefix("[排除] ").strip() for r in rows if r.content.startswith("[排除]")]

    async def _repair_reply(self, tools: ToolBox, inc: Any, user_message: str) -> AgentReply:
        ctx = self.ctx
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

        sop_chunks: list[dict] = []
        if current:
            for phase in ("inspect", "repair"):
                sop_chunks += await tools.call("get_sop", {"failure_mode_id": current["id"], "phase": phase})
        cases = await tools.call("search_cases", {"query": f"{inc.type} {current['id'] if current else ''}"})
        signals = [await tools.call("get_machine_signals", {"signal": s, "last_min": 10})
                   for s in ("feed_rate", "feed_length_error_mm", "motor_current_a")] if inc.machine_id else []
        context = {"workorder": info, "current_hypothesis": current, "rejected_hypotheses": rejected,
                   "sop": [{"sop_id": c["sop_id"], "phase": c["phase"], "text": c["text"]} for c in sop_chunks],
                   "similar_cases": [c["short"] for c in cases], "signals": signals}
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "system", "content": "工單資料（JSON）：\n" + json.dumps(context, ensure_ascii=False)}]
        for m in self.history(tools.wo_id)[-9:-1]:
            if m.role in ("user", "assistant"):
                messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": user_message})

        try:
            content = await self._llm_loop(messages, tools, inc.trace_id)
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
                          hypothesis=current["id"] if current else None, llm_used=used)

    async def _llm_loop(self, messages: list[dict], tools: ToolBox, trace_id: str) -> str:
        allowed = guards.allowed_tools(tools.status(), self.ctx.require_permit) - {"request_supervisor"}
        for _ in range(MAX_TOOL_ROUNDS):
            res = await self.ctx.llm.chat(messages, tools=schemas_for(allowed), purpose="agent.reply", trace_id=trace_id)
            if not res.tool_calls:
                return res.content.strip()
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
        res = await self.ctx.llm.chat(messages, purpose="agent.reply", trace_id=trace_id)
        return res.content.strip()

    # ── 3. J4 安全把關 ─────────────────────────────────────────
    async def _gate(self, reply: AgentReply, tools: ToolBox, inc: Any) -> None:
        ctx = self.ctx
        m = ctx.plant.machines.get(inc.machine_id or "")
        thr = ctx.thresholds.safety_gate_p
        steps = guards.extract_steps(reply.content)

        async def check(text: str) -> StepCheck:
            try:
                g = await gate.step(ctx.jev, text, m.type if m else None, threshold=thr, trace_id=inc.trace_id)
                return StepCheck(text=text, flagged=g.flagged, needs_loto=g.needs_loto,
                                 enters_danger_zone=g.enters_danger_zone, high_voltage=g.high_voltage)
            except JevUnavailable:
                p = guards.keyword_gate(text)
                return StepCheck(text=text, flagged=any(v > thr for v in p.values()), source="keyword", **p)

        reply.steps = list(await asyncio.gather(*(check(s) for s in steps)))
        flagged = [s for s in reply.steps if s.flagged]
        if flagged:
            reply.safety_flags.append("j4_flagged")
            reply.supervisor_requested = True
            reasons = "；".join(f"「{s.text[:40]}」" for s in flagged[:3])
            await tools.call("request_supervisor", {"reason": f"維修步驟涉及危險操作，請主管確認：{reasons}"})

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


def message_dict(m: AgentMessage) -> dict:
    """前端 AgentMessage 格式。"""
    return {"id": str(m.id), "workorder_id": m.workorder_id, "role": m.role, "content": m.content,
            "steps": guards.extract_steps(m.content) if m.role == "assistant" else [],
            "tool_calls": m.tool_calls or [], "safety_flags": m.safety_flags or [], "ts": m.ts.isoformat()}
