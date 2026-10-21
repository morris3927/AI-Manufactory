"""知識回寫審核（CL-3）：列出結案案例草稿、主管核准後立即入庫。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db.models import Workorder
from ..kb.writeback import summary
from ..schemas.events import Event, Topic
from ..sim.roster import ROLE_RANK
from .deps import get_rt, resolve_actor

router = APIRouter()


class ApproveReq(BaseModel):
    person_id: str | None = None
    note: str = "現場主管核准"


@router.get("/kb/cases")
def list_cases(request: Request, status: str | None = None) -> list[dict]:
    """status：draft／approved；不填回全部。"""
    return get_rt(request).writeback.list(status)


@router.post("/kb/cases/{case_id}/approve")
async def approve_case(case_id: str, request: Request, body: ApproveReq | None = None) -> dict:
    rt = get_rt(request)
    doc = rt.writeback.get(case_id)
    if doc is None:
        raise HTTPException(404, f"找不到案例 {case_id}")
    actor = resolve_actor(request, body.person_id if body else None, doc.get("line"))
    person = rt.roster.person(actor)
    if person is None or ROLE_RANK.get(person.role, 0) < ROLE_RANK["supervisor"]:
        raise HTTPException(403, "案例入庫需要組長（supervisor）以上核准")
    if doc.get("review_status") == "approved":
        raise HTTPException(409, f"{case_id} 已經核准入庫")
    try:
        doc, n = rt.writeback.approve(case_id, reviewer=actor, at=rt.clock.now(),
                                      note=body.note if body else "現場主管核准")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    wo_id = (doc.get("generator") or {}).get("workorder_id")
    wo = rt.db.get(Workorder, wo_id) if wo_id else None
    inc = rt.workorders.incident(wo) if wo else None
    rt.audit.record("case_approved", actor=actor, trace_id=inc.trace_id if inc else None, case_id=case_id,
                    workorder_id=wo_id, chunks=n, final_root_cause=doc.get("final_root_cause"))
    if wo and inc:
        await rt.workorders.publish(wo, inc, "case_approved", {"case_id": case_id})
    await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.AI, source="kb", line=doc.get("line"),
                               machine_id=doc.get("machine_id"), type="case_approved",
                               trace_id=inc.trace_id if inc else None,
                               payload={"case_id": case_id, "workorder_id": wo_id, "chunks": n}))
    return {"ok": True, "case": summary(doc), "ingested_chunks": n}
