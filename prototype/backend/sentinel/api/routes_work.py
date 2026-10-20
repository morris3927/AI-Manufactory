"""異常與工單（Kanban、任務詳情、技術員操作）。POST 操作一律回傳更新後的工單詳情（WorkOrderDetail）。"""
from __future__ import annotations

import functools

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlmodel import col, select

from ..db.models import Incident, Workorder
from ..dispatch.closeout import submit_report
from ..dispatch.workorder import PermissionDenied, TransitionError
from ..schemas.api import ActorReq, AgentReq, CheckReq, PermitReq, ReassignReq, ReportReq
from .deps import base_url, get_rt, resolve_actor
from .serialize import incident_out, workorder_detail, workorder_out

router = APIRouter()


def _guard(fn):
    """把狀態機例外轉成 HTTP 錯誤。"""

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"找不到 {e}") from e
        except TransitionError as e:
            raise HTTPException(409, str(e)) from e
        except PermissionDenied as e:
            raise HTTPException(403, str(e)) from e

    return wrapper


def _line(request: Request, wo_id: str) -> str | None:
    rt = get_rt(request)
    wo = rt.db.get(Workorder, wo_id)
    if wo is None:
        raise HTTPException(404, f"找不到 {wo_id}")
    inc = rt.db.get(Incident, wo.incident_id)
    return inc.line if inc else None


def _detail(request: Request, wo_id: str) -> dict:
    return workorder_detail(get_rt(request), wo_id, base_url(request))


# ── Incident ────────────────────────────────────────────────────
@router.get("/incidents")
def incidents(request: Request, status: str | None = None, line: str | None = None, limit: int = 100) -> list[dict]:
    rt = get_rt(request)
    stmt = select(Incident).order_by(col(Incident.opened_at).desc()).limit(limit)
    if status:
        stmt = stmt.where(Incident.status == status)
    if line:
        stmt = stmt.where(Incident.line == line)
    return [incident_out(rt, i, base_url(request)) for i in rt.db.all(stmt)]


@router.get("/incidents/{incident_id}")
def incident(incident_id: str, request: Request) -> dict:
    rt = get_rt(request)
    inc = rt.db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, f"找不到 {incident_id}")
    wo = rt.workorders.for_incident(incident_id)
    return incident_out(rt, inc, base_url(request)) | {
        "workorder_id": wo.id if wo else None,
        "timeline": list(rt.windows.timeline.get(inc.machine_id or inc.camera_id or "", []))}


@router.get("/incidents/{incident_id}/keyframe")
def keyframe(incident_id: str, request: Request) -> FileResponse:
    path = get_rt(request).keyframe_path(incident_id)
    if not path.exists():
        raise HTTPException(404, "沒有關鍵幀")
    return FileResponse(path, media_type="image/jpeg")


# ── Workorder ───────────────────────────────────────────────────
@router.get("/workorders")
def workorders(request: Request, status: str | None = None, line: str | None = None) -> list[dict]:
    rt = get_rt(request)
    stmt = select(Workorder).order_by(col(Workorder.created_at).desc())
    if status:
        stmt = stmt.where(col(Workorder.status).in_(status.split(",")))
    out = [workorder_out(rt, wo) for wo in rt.db.all(stmt)]
    return [w for w in out if not line or w.get("line") == line]


@router.get("/workorders/{wo_id}")
def workorder(wo_id: str, request: Request) -> dict:
    try:
        return _detail(request, wo_id)
    except KeyError as e:
        raise HTTPException(404, f"找不到 {wo_id}") from e


@router.post("/workorders/{wo_id}/ack")
@_guard
async def ack(wo_id: str, request: Request, body: ActorReq | None = None) -> dict:
    actor = resolve_actor(request, body.person_id if body else None, _line(request, wo_id))
    await get_rt(request).workorders.ack(wo_id, actor)
    return _detail(request, wo_id)


@router.post("/workorders/{wo_id}/reassign")
@_guard
async def reassign(wo_id: str, body: ReassignReq, request: Request) -> dict:
    rt = get_rt(request)
    actor = resolve_actor(request, body.person_id, _line(request, wo_id))
    wo = rt.workorders.get(wo_id)
    target = rt.roster.person(body.to_person_id) if body.to_person_id else None
    if body.to_person_id and target is None:
        raise HTTPException(404, f"找不到人員 {body.to_person_id}")
    if target is None:                                    # 沒指定 → 交給系統依職位往上一級
        if wo.status != "ASSIGNED" or not await rt.escalation.escalate(wo):
            raise HTTPException(409, "目前狀態無法轉派，或已到最高職位")
        return _detail(request, wo_id)
    inc = rt.workorders.incident(wo)
    wo = await rt.workorders.assign(wo_id, person_id=target.id, role=target.role, actor=actor, reason=body.reason,
                                    ack_timeout_s=rt.settings.escalation.timeout_for(inc.final_severity))
    await rt.dispatch.notify(wo, inc, rt.dispatch.diagnosis_for(inc.id), None, kind="dispatch",
                             extra=[f"轉派原因：{body.reason}"])
    return _detail(request, wo_id)


@router.post("/workorders/{wo_id}/loto/{step}")
@_guard
async def loto(wo_id: str, step: int, request: Request, body: CheckReq | None = None) -> dict:
    rt = get_rt(request)
    actor = resolve_actor(request, body.person_id if body else None, _line(request, wo_id))
    if body and body.checked is False:
        raise HTTPException(409, "LOTO 紀錄只能新增，不能取消勾選（稽核需要）")
    checklist = rt.agent.ctx.loto_for(wo_id)
    await rt.workorders.check_loto(wo_id, step, actor, loto_id=checklist.loto_id, steps=[s.no for s in checklist.steps])
    return _detail(request, wo_id)


@router.post("/workorders/{wo_id}/permit")
@_guard
async def permit(wo_id: str, body: PermitReq, request: Request) -> dict:
    actor = resolve_actor(request, body.person_id, _line(request, wo_id))
    await get_rt(request).workorders.permit(wo_id, actor, body.action)
    return _detail(request, wo_id)


@router.post("/workorders/{wo_id}/checklist/{item}")
@_guard
async def checklist(wo_id: str, item: str, request: Request, body: CheckReq | None = None) -> dict:
    """item：checklist 的索引（0 起算，即 detail.checklist[].item）或文字。"""
    rt = get_rt(request)
    actor = resolve_actor(request, body.person_id if body else None, _line(request, wo_id))
    if body and body.checked is False:
        raise HTTPException(409, "Checklist 紀錄只能新增，不能取消勾選")
    wo = rt.workorders.get(wo_id)
    diag = rt.dispatch.diagnosis_for(wo.incident_id)
    items = rt.kb.checklist_for((diag.root_cause_top3 or [{}])[0].get("id") if diag else None)
    text = items[int(item)] if item.isdigit() and int(item) < len(items) else item
    await rt.workorders.check_item(wo_id, text, actor, items)
    return _detail(request, wo_id)


@router.post("/workorders/{wo_id}/report")
@_guard
async def report(wo_id: str, body: ReportReq, request: Request) -> dict:
    actor = resolve_actor(request, body.person_id, _line(request, wo_id))
    res = await submit_report(get_rt(request), wo_id, actor, body.text, body.root_cause_confirmed or body.root_cause)
    return res | {"workorder": _detail(request, wo_id)}


@router.post("/workorders/{wo_id}/agent")
@_guard
async def agent(wo_id: str, body: AgentReq, request: Request) -> dict:
    """背景處理；使用者訊息與回覆都推送到 WebSocket 頻道 agent:{workorder_id}（message／delta／safety_gate）。"""
    rt = get_rt(request)
    actor = resolve_actor(request, body.person_id, _line(request, wo_id))
    rt.workorders.get(wo_id)
    if body.wait:
        reply = await rt.agent.handle(wo_id, body.message, actor)
        return {"accepted": True, "message_id": reply.message_id, "reply": reply.model_dump()}
    rt.bus.track(rt.agent.handle(wo_id, body.message, actor), name=f"agent:{wo_id}")
    return {"accepted": True}
