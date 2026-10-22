"""結案（CL-1、CL-3；J5）：回報品質檢查 → 寫回知識庫草稿（review_status=draft）→ CLOSED。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..db.models import Diagnosis
from ..jev import gate
from ..jev.client import JevUnavailable
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
    """CL-3：結案回報 → 案例草稿（review_status=draft）；主管在 /api/kb/cases/{id}/approve 核准後立即入庫。"""
    from ..kb.writeback import build_case, case_id_for

    wo = rt.workorders.get(wo_id)
    inc = rt.workorders.incident(wo)
    now = rt.clock.now()
    case_id = case_id_for(inc.line, wo_id, now)
    doc = build_case(case_id=case_id, inc=inc, machine=rt.plant.machines.get(inc.machine_id or ""), wo_id=wo_id,
                     report=report, final_rc=final_rc, diag=diag, closed_at=now, reported_by=reported_by,
                     timeline=list(rt.windows.timeline.get(inc.machine_id or inc.camera_id or "", [])))
    try:
        path = rt.writeback.write(doc)
    except OSError:
        log.exception("案例草稿寫入失敗")
        return None, None
    rt.audit.record("case_draft_written", trace_id=inc.trace_id, workorder_id=wo_id, case_id=case_id)
    return case_id, path
