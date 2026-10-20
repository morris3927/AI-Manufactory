"""結案（CL-1、CL-3；J5）：回報品質檢查 → 寫回知識庫草稿（review_status=draft）→ CLOSED。"""
from __future__ import annotations

import json
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
    check = None
    try:
        check = await gate.writeback(rt.jev, text, inc.line, fms, threshold=rt.settings.thresholds.writeback_complete,
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
    draft_path = write_case_draft(rt, wo_id, text, final_rc, diag)
    wo = await rt.workorders.transition(wo_id, WoStatus.CLOSED, actor=person_id, reason="回報通過")
    if check and check.new_failure_mode:
        rt.audit.record("new_failure_mode_suspected", actor="jev", trace_id=inc.trace_id, workorder_id=wo_id)
        await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.AI, source="jev", line=inc.line,
                                   machine_id=inc.machine_id, type="new_failure_mode", trace_id=inc.trace_id,
                                   payload={"workorder_id": wo_id, "report": text[:200]}))
    check_out = {"complete": True, "missing": [], "score": check.complete if check else None,
                 "kb_status": "draft" if draft_path else None, "jev_checked": check is not None}
    _store_report(rt, wo_id, text, final_rc, person_id, check_out)
    return {"ok": True, "status": wo.status, "final_root_cause": final_rc, "check": check_out,
            "case_draft": str(draft_path.name) if draft_path else None}


def _store_report(rt: "Runtime", wo_id: str, text: str, root_cause: str | None, person_id: str, check: dict) -> None:
    from ..db.models import Workorder

    with rt.db.session() as s:
        wo = s.get(Workorder, wo_id)
        if wo:
            wo.report = {"text": text, "root_cause_confirmed": root_cause, "submitted_by": person_id,
                         "submitted_at": rt.clock.now().isoformat(), "check": check}
            s.add(wo)
            s.commit()


def write_case_draft(rt: "Runtime", wo_id: str, report: str, final_rc: str | None, diag: Diagnosis | None):
    """CL-3（簡易版）：結案回報 → 案例草稿 JSON；人工審核改成 approved 後 make kb 才會入庫。"""
    wo = rt.workorders.get(wo_id)
    inc = rt.workorders.incident(wo)
    m = rt.plant.machines.get(inc.machine_id or "")
    top1 = (diag.root_cause_top3 or [None])[0] if diag else None
    case_id = f"CASE-DRAFT-{wo_id}"
    doc = {
        "case_id": case_id, "data_label": "模擬資料", "synthetic": True,
        "generator": {"source": "writeback", "workorder_id": wo_id},
        "line": inc.line, "machine_type": m.type if m else None, "machine_id": inc.machine_id,
        "opened_at": inc.opened_at.isoformat(), "closed_at": rt.clock.now().isoformat(),
        "trigger": {"source": inc.facts.get("source", "fused"), "event_type": inc.type,
                    "rule_severity": inc.rule_severity},
        "technician_report_raw": report,
        "first_hypothesis": {"failure_mode_id": top1.get("id"), "by": "jev", "p": top1.get("p")} if top1 else None,
        "final_root_cause": final_rc,
        "hypothesis_correct": (top1.get("id") == final_rc) if top1 and final_rc else None,
        "sop_used": diag.sop_ids if diag else [],
        "review_status": "draft",
    }
    try:
        d = rt.settings.path("drafts") / inc.line
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{case_id}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        rt.audit.record("case_draft_written", trace_id=inc.trace_id, workorder_id=wo_id, case_id=case_id)
        return path
    except OSError:
        log.exception("案例草稿寫入失敗")
        return None
