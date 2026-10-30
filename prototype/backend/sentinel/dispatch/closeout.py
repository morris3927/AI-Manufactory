"""結案（CL-1、CL-3；J5）：回報品質檢查 → 寫回知識庫草稿（review_status=draft）→ CLOSED。

案例草稿分兩段（結案不等 LLM）：
1. 結案當下用程式組稿（kb/writeback.build_case，drafted_by=rules），立即寫檔、工單 CLOSED；
2. 背景請 LLM 把回報、工單資訊、確認的根因、Jev 診斷整理成摘要／原因／處置步驟／注意事項（llm/case_draft.py），
   成功且草稿還沒被核准 → 改寫草稿並標 drafted_by=llm；LLM 不可用、逾時（llm.case_draft_timeout_s）或
   回覆不合格（無法解析、出現資料裡沒有的數值）→ 保留程式版，llm_draft.status=failed。兩種結果都記稽核。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..db.models import Diagnosis
from ..jev import gate
from ..jev.client import JevUnavailable
from ..llm import case_draft
from ..llm.client import LlmUnavailable
from ..schemas.events import Event, Topic
from .workorder import TransitionError, WoStatus

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)


async def submit_report(rt: "Runtime", wo_id: str, person_id: str, text: str,
                        root_cause: str | None = None) -> dict[str, Any]:
    wo = rt.workorders.get(wo_id)
    inc = rt.workorders.incident(wo)
    rt.audit.record("report_submitted", actor=person_id, trace_id=inc.trace_id, workorder_id=wo_id, chars=len(text))

    if wo.category == "safety":                       # 安全類：ACKED → CLOSED，不需要 J5
        check_out = {"complete": True, "missing": [], "score": None, "kb_status": None}
        wo = await rt.workorders.transition(wo_id, WoStatus.CLOSED, actor=person_id, reason=f"回報：{text[:60]}")
        _store_report(rt, wo_id, text, root_cause, person_id, check_out)
        return {"ok": True, "status": wo.status, "check": check_out}

    if wo.status != WoStatus.PENDING_VERIFY:
        raise TransitionError(f"結案回報需要先完成 Checklist（目前 {wo.status}）")
    fms = rt.kb.failure_modes_for(inc.line)
    # 技術員在下拉選單選的根因也算回報內容（否則只寫處置的回報會被 J5 判「缺少實際故障原因」）
    fm = fms.get(root_cause) if root_cause else None
    gate_text = f"實際故障原因（技術員確認）：{fm.get('label') or root_cause}。\n{text}" if fm else text
    check = None
    try:
        check = await gate.writeback(rt.jev, gate_text, inc.line, fms, threshold=rt.settings.thresholds.writeback_complete,
                                     trace_id=inc.trace_id)
    except JevUnavailable as e:
        rt.audit.record("jev_unavailable", actor="jev", trace_id=inc.trace_id, purpose="writeback", error=str(e))
    if check is not None and not check.ok:
        rt.audit.record("report_rejected", actor="jev", trace_id=inc.trace_id, workorder_id=wo_id,
                        complete=check.complete, missing=check.missing)
        check_out = {"complete": False, "missing": check.missing, "score": check.complete, "kb_status": "rejected"}
        _store_report(rt, wo_id, text, root_cause, person_id, check_out)
        return {"ok": False, "status": wo.status, "check": check_out,
                "reason": "回報需要同時寫出：" + "、".join(check.missing)}

    diag = rt.dispatch.diagnosis_for(inc.id)
    final_rc = root_cause or (check.root_cause if check and not check.new_failure_mode else None) or \
        ((diag.root_cause_top3 or [{}])[0].get("id") if diag else None)
    if diag:
        with rt.db.session() as s:
            row = s.get(Diagnosis, diag.id)
            if row:
                row.final_root_cause = final_rc
                s.add(row)
                s.commit()
    case_id, draft_path = write_case_draft(rt, wo_id, text, final_rc, diag, person_id)
    wo = await rt.workorders.transition(wo_id, WoStatus.CLOSED, actor=person_id, reason="回報通過")
    if check and check.new_failure_mode:
        rt.audit.record("new_failure_mode_suspected", actor="jev", trace_id=inc.trace_id, workorder_id=wo_id)
        await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.AI, source="jev", line=inc.line,
                                   machine_id=inc.machine_id, type="new_failure_mode", trace_id=inc.trace_id,
                                   payload={"workorder_id": wo_id, "report": text[:200]}))
    check_out = {"complete": True, "missing": [], "score": check.complete if check else None,
                 "kb_status": "draft" if draft_path else None, "jev_checked": check is not None, "case_id": case_id}
    _store_report(rt, wo_id, text, final_rc, person_id, check_out)
    return {"ok": True, "status": wo.status, "final_root_cause": final_rc, "check": check_out,
            "case_draft": case_id}


def _store_report(rt: "Runtime", wo_id: str, text: str, root_cause: str | None, person_id: str, check: dict) -> None:
    from ..db.models import Workorder

    with rt.db.session() as s:
        wo = s.get(Workorder, wo_id)
        if wo:
            wo.report = {"text": text, "root_cause_confirmed": root_cause, "submitted_by": person_id,
                         "submitted_at": rt.clock.now().isoformat(), "check": check}
            s.add(wo)
            s.commit()


def write_case_draft(rt: "Runtime", wo_id: str, report: str, final_rc: str | None, diag: Diagnosis | None,
                     reported_by: str = "") -> tuple[str, object] | tuple[None, None]:
    """CL-3：結案回報 → 案例草稿（review_status=draft）；主管在 /api/kb/cases/{id}/approve 核准後立即入庫。
    先寫程式版草稿（不等 LLM），LLM 啟用時再於背景整理（_llm_redraft）。"""
    from ..kb.writeback import build_case, case_id_for

    wo = rt.workorders.get(wo_id)
    inc = rt.workorders.incident(wo)
    now = rt.clock.now()
    case_id = case_id_for(inc.line, wo_id, now)
    machine = rt.plant.machines.get(inc.machine_id or "")
    doc = build_case(case_id=case_id, inc=inc, machine=machine, wo_id=wo_id,
                     report=report, final_rc=final_rc, diag=diag, closed_at=now, reported_by=reported_by,
                     timeline=list(rt.windows.timeline.get(inc.machine_id or inc.camera_id or "", [])))
    use_llm = rt.llm.enabled
    doc["llm_draft"] = {"status": "pending"} if use_llm else {"status": "skipped", "reason": "LLM 未啟用"}
    try:
        path = rt.writeback.write(doc)
    except OSError:
        log.exception("案例草稿寫入失敗")
        return None, None
    rt.audit.record("case_draft_written", trace_id=inc.trace_id, workorder_id=wo_id, case_id=case_id,
                    drafted_by="rules", llm=doc["llm_draft"]["status"])
    if use_llm:
        labels = {k: v.get("label") or k for k, v in rt.kb.failure_modes_for(inc.line).items()}
        msgs = case_draft.build_messages(wo_id=wo_id, inc=inc, machine_name=machine.name if machine else None,
                                         report=report, final_rc=final_rc, rc_label=labels.get(final_rc or ""),
                                         diag=diag, labels=labels)
        rt.bus.track(_llm_redraft(rt, case_id, wo_id, msgs), name=f"llm.case_draft:{case_id}")
    return case_id, path


async def _llm_redraft(rt: "Runtime", case_id: str, wo_id: str, messages: list[dict]) -> None:
    """背景：LLM 整理案例草稿。失敗只記稽核、保留程式版；草稿已被核准就不改（主管核准的是當時看到的版本）。"""
    wo = rt.workorders.get(wo_id)
    inc = rt.workorders.incident(wo)
    fields, res, reason = None, None, None
    try:
        fields, res = await asyncio.wait_for(case_draft.draft(rt.llm, messages, trace_id=inc.trace_id),
                                             timeout=rt.settings.llm.case_draft_timeout_s)
    except (TimeoutError, asyncio.TimeoutError):
        reason = "timeout"
    except LlmUnavailable as e:
        reason = f"LLM 不可用：{e}"
    except case_draft.CaseDraftInvalid as e:
        reason = f"回覆不合格：{e}"
    doc = rt.writeback.get(case_id)
    if doc is None or doc.get("review_status") != "draft":
        rt.audit.record("case_draft_llm", actor="llm", trace_id=inc.trace_id, workorder_id=wo_id, case_id=case_id,
                        ok=False, drafted_by=(doc or {}).get("drafted_by"), reason="草稿已核准或不存在，LLM 版不採用")
        return
    at = rt.clock.now().isoformat()
    if fields is not None and res is not None:
        doc.update(fields)
        doc["drafted_by"] = "llm"
        doc["llm_draft"] = {"status": "ok", "model": res.model, "prompt_ver": case_draft.PROMPT_VER,
                            "latency_ms": round(res.latency_ms or 0), "at": at}
    else:
        doc["llm_draft"] = {"status": "failed", "reason": reason, "at": at}
    try:
        rt.writeback.write(doc)
    except OSError:
        log.exception("案例草稿更新失敗")
        return
    rt.audit.record("case_draft_llm", actor="llm", trace_id=inc.trace_id, workorder_id=wo_id, case_id=case_id,
                    ok=fields is not None, drafted_by=doc["drafted_by"], reason=reason,
                    model=res.model if res else None, latency_ms=round(res.latency_ms or 0) if res else None)
    await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.AI, source="llm", line=inc.line,
                               machine_id=inc.machine_id, type="case_draft_updated", trace_id=inc.trace_id,
                               payload={"case_id": case_id, "workorder_id": wo_id, "incident_id": inc.id,
                                        "drafted_by": doc["drafted_by"], "llm_status": doc["llm_draft"]["status"]}))
