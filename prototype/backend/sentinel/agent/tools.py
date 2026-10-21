"""維修 Agent 可以呼叫的工具。每次呼叫都先經過 guards.check_tool（依工單狀態開放）。

刻意沒有任何「改變工單狀態」的工具：狀態只能由人在介面上操作。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..db.models import AgentMessage
from ..ids import jsonable
from .guards import check_tool

if TYPE_CHECKING:
    from .repair_agent import AgentContext

TOOL_SCHEMAS: dict[str, dict] = {
    "get_workorder": {"description": "取得工單、Incident 與 Jev 根因前三名", "parameters": {"type": "object", "properties": {}}},
    "get_loto": {"description": "取得這台機器的 LOTO 檢查表與勾選進度", "parameters": {"type": "object", "properties": {}}},
    "get_machine_signals": {
        "description": "查詢機台 Log 訊號的摘要（已算好的統計，不是原始資料）",
        "parameters": {"type": "object", "properties": {
            "signal": {"type": "string", "description": "例如 feed_rate、feed_length_error_mm、motor_temp_c"},
            "last_min": {"type": "number", "description": "最近幾分鐘，預設 10"}}, "required": ["signal"]}},
    "get_sop": {
        "description": "取得某個故障模式的 SOP 段落",
        "parameters": {"type": "object", "properties": {
            "failure_mode_id": {"type": "string"},
            "phase": {"type": "string", "enum": ["summary", "prepare", "isolate", "inspect", "repair", "verify", "restore"]}},
            "required": ["failure_mode_id", "phase"]}},
    "search_cases": {"description": "找相似的歷史維修案例（包含第一判斷錯誤的案例）",
                     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    "record_step": {"description": "記錄技術員回報的步驟結果",
                    "parameters": {"type": "object", "properties": {"step": {"type": "string"}, "result": {"type": "string"}},
                                   "required": ["step", "result"]}},
    "request_supervisor": {"description": "要求主管確認（涉及危險操作或無法判斷時）",
                           "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
}


def schemas_for(names: set[str]) -> list[dict]:
    return [{"type": "function", "function": {"name": n, **TOOL_SCHEMAS[n]}} for n in TOOL_SCHEMAS if n in names]


class ToolBox:
    def __init__(self, ctx: "AgentContext", workorder_id: str):
        self.ctx = ctx
        self.wo_id = workorder_id

    def status(self) -> str:
        return self.ctx.workorders.get(self.wo_id).status

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        args = args or {}
        check_tool(name, args, self.status(), self.ctx.require_permit)     # 程式強制：不靠提示詞
        fn = getattr(self, f"_t_{name}")
        result = await fn(**args)
        self.ctx.audit.record("agent_tool", actor=f"agent:{self.wo_id}", trace_id=self._trace(), tool=name, args=args)
        return jsonable(result)

    def _trace(self) -> str:
        return self.ctx.workorders.incident(self.ctx.workorders.get(self.wo_id)).trace_id

    # ── 工具實作 ───────────────────────────────────────────────
    async def _t_get_workorder(self) -> dict:
        wo = self.ctx.workorders.get(self.wo_id)
        inc = self.ctx.workorders.incident(wo)
        diag = self.ctx.dispatch.diagnosis_for(inc.id)
        m = self.ctx.plant.machines.get(inc.machine_id or "")
        return {"workorder_id": wo.id, "status": wo.status, "severity": wo.severity, "category": wo.category,
                "machine": {"id": inc.machine_id, "type": m.type if m else None, "name": m.name if m else None},
                "incident": {"id": inc.id, "type": inc.type, "facts": inc.facts},
                "root_cause_top3": diag.root_cause_top3 if diag else [], "sop_ids": diag.sop_ids if diag else []}

    async def _t_get_loto(self) -> dict:
        loto = self.ctx.loto_for(self.wo_id)
        done = self.ctx.workorders.loto_checked(self.wo_id)
        return {"loto_id": loto.loto_id, "steps": [s.model_dump() | {"checked": s.no in done} for s in loto.steps],
                "permit_required": loto.permit_required, "energy_sources": loto.energy_sources}

    async def _t_get_machine_signals(self, signal: str, last_min: float = 10) -> dict:
        inc = self.ctx.workorders.incident(self.ctx.workorders.get(self.wo_id))
        return self.ctx.windows.summary(inc.machine_id or inc.camera_id or "", signal, last_min)

    async def _t_get_sop(self, failure_mode_id: str, phase: str) -> list[dict]:
        return [c.model_dump() for c in self.ctx.kb.sops_for([failure_mode_id], phases=[phase])]

    async def _t_search_cases(self, query: str) -> list[dict]:
        inc = self.ctx.workorders.incident(self.ctx.workorders.get(self.wo_id))
        return [c.model_dump() for c in self.ctx.kb.similar_cases(inc, k=3, query=query)]

    async def _t_record_step(self, step: str, result: str) -> dict:
        self.ctx.db.add(AgentMessage(workorder_id=self.wo_id, role="tool", content=f"[步驟] {step} → {result}",
                                     tool_calls=[{"name": "record_step", "args": {"step": step, "result": result}}],
                                     ts=self.ctx.clock.now()))
        return {"ok": True}

    async def _t_request_supervisor(self, reason: str) -> dict:
        await self.ctx.dispatch.request_supervisor(self.wo_id, reason, actor=f"agent:{self.wo_id}")
        return {"ok": True}


def tool_result_message(name: str, result: Any) -> dict:
    return {"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False)[:4000]}
