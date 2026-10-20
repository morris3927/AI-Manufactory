"""覆寫、稽核、效益指標、控制台（劇本、重置、Demo 設定）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..db.models import AgentMessage, Incident, Override, Workorder
from ..schemas.events import Event, Topic
from ..dispatch.workorder import PermissionDenied, TransitionError, WoStatus
from ..eval import metrics
from ..schemas.api import DemoSettingsReq, OverrideReq
from ..sim.roster import ROLE_RANK
from .deps import get_rt, resolve_actor

router = APIRouter()


@router.post("/overrides")
async def override(body: OverrideReq, request: Request) -> dict:
    """GV-3：主管覆寫（嚴重度、改派、標記誤報、J4 安全把關確認）；原因必填。"""
    rt = get_rt(request)
    if body.target == "incident":
        inc = rt.db.get(Incident, body.target_id)
        wo = rt.workorders.for_incident(body.target_id) if inc else None
    else:
        wo = rt.db.get(Workorder, body.target_id)
        inc = rt.db.get(Incident, wo.incident_id) if wo else None
    if inc is None:
        raise HTTPException(404, f"找不到 {body.target_id}")
    actor_id = resolve_actor(request, body.person_id, inc.line)
    actor = rt.roster.person(actor_id)
    if actor is None or ROLE_RANK.get(actor.role, 0) < ROLE_RANK["leader"]:
        raise HTTPException(403, "只有班長以上可以覆寫")
    field = "final_severity" if body.field == "severity" else body.field
    old = None
    try:
        if field == "final_severity":
            if body.new not in ("S1", "S2", "S3"):
                raise HTTPException(422, "嚴重度必須是 S1／S2／S3")
            old = inc.final_severity
            with rt.db.session() as s:
                row = s.get(Incident, inc.id)
                row.final_severity = body.new
                s.add(row)
                if wo:
                    w = s.get(Workorder, wo.id)
                    w.severity = body.new
                    s.add(w)
                s.commit()
        elif field == "false_alarm":
            if wo is None:
                raise HTTPException(409, "沒有工單")
            old = wo.status
            await rt.workorders.transition(wo.id, WoStatus.CANCELLED, actor=actor_id, reason=f"標記誤報：{body.reason}")
        elif field == "assignee":
            target = rt.roster.person(str(body.new))
            if wo is None or target is None:
                raise HTTPException(404, "找不到工單或人員")
            old = wo.assignee_id
            await rt.workorders.assign(wo.id, person_id=target.id, role=target.role, actor=actor_id,
                                       reason=f"主管改派：{body.reason}",
                                       ack_timeout_s=rt.settings.escalation.timeout_for(inc.final_severity))
        elif field == "safety_ack":                    # J4 標紅步驟由主管確認；不改工單狀態
            if ROLE_RANK.get(actor.role, 0) < ROLE_RANK["supervisor"]:
                raise HTTPException(403, "安全把關需要組長（supervisor）以上確認")
            old = await ack_safety(rt, wo, str(body.new), actor_id, body.reason)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    except PermissionDenied as e:
        raise HTTPException(403, str(e)) from e
    row = Override(target=body.target, target_id=body.target_id, field=field, old=old, new=body.new,
                   reason=body.reason, actor=actor_id, ts=rt.clock.now())
    rt.db.add(row)
    rt.audit.record("override", actor=actor_id, trace_id=inc.trace_id, field=field, old=old, new=body.new,
                    reason=body.reason)
    wo = rt.workorders.for_incident(inc.id)
    if wo:
        await rt.workorders.publish(wo, inc, "override", {"field": field})
    return {"ok": True, "override": row.model_dump(mode="json")}


async def ack_safety(rt, wo, message_id: str, actor_id: str, reason: str):
    if wo is None or not message_id.isdigit():
        raise HTTPException(422, "safety_ack 需要工單與 Agent 訊息 ID")
    with rt.db.session() as s:
        m = s.get(AgentMessage, int(message_id))
        if m is None or m.workorder_id != wo.id:
            raise HTTPException(404, f"找不到訊息 {message_id}")
        old = [dict(f) for f in m.safety_flags or []]
        m.safety_flags = [dict(f, supervisor_requested=False, acked_by=actor_id, ack_reason=reason)
                          for f in m.safety_flags or []]
        s.add(m)
        s.commit()
        flags = m.safety_flags
    inc = rt.workorders.incident(wo)
    await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.AI, source="supervisor", line=inc.line,
                               machine_id=inc.machine_id, type="safety_ack", trace_id=inc.trace_id,
                               payload={"workorder_id": wo.id, "message_id": message_id, "flags": flags}))
    return old


@router.get("/audit")
def audit(request: Request, trace_id: str | None = None, workorder_id: str | None = None, limit: int = 500) -> list[dict]:
    rt = get_rt(request)
    if workorder_id and not trace_id:
        wo = rt.db.get(Workorder, workorder_id)
        if wo is None:
            raise HTTPException(404, f"找不到 {workorder_id}")
        trace_id = rt.workorders.incident(wo).trace_id
    return [a.model_dump() for a in rt.audit.timeline(trace_id, limit)]


@router.get("/metrics/summary")
def metrics_summary(request: Request) -> dict:
    return metrics.summary(get_rt(request).db)


@router.get("/metrics/calibration")
def metrics_calibration(request: Request) -> dict:
    return metrics.calibration(get_rt(request).db)


@router.get("/scenarios")
def scenarios(request: Request) -> list[dict]:
    rt = get_rt(request)
    current = rt.engine.scenario.id if rt.engine.scenario else None
    return [{"id": sc.id, "title": sc.title, "description": sc.title, "duration_s": sc.duration_s,
             "running": sc.id == current, "expect": sc.expect.model_dump()} for sc in rt.scenarios.values()]


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(scenario_id: str, request: Request) -> dict:
    rt = get_rt(request)
    if scenario_id not in rt.scenarios:
        raise HTTPException(404, f"找不到劇本 {scenario_id}")
    sc = await rt.run_scenario(scenario_id)
    return {"ok": True, "scenario": sc.id, "title": sc.title,
            "started_at": rt.engine.scenario_started_at.isoformat() if rt.engine.scenario_started_at else None}


@router.post("/demo/reset")
async def demo_reset(request: Request) -> dict:
    rt = get_rt(request)
    await rt.reset()
    await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.TASKS, source="demo", type="reset", payload={}))
    return {"ok": True}


@router.get("/videos")
def videos(request: Request) -> list[dict]:
    """影片素材清單：assets/videos 下的 mp4，加上劇本引用但還沒備齊的（以合成畫面播放）。"""
    rt = get_rt(request)
    root = rt.settings.path("videos_root")
    vids: dict[str, dict] = {}
    vdir = root / "assets" / "videos"
    for p in sorted(vdir.glob("**/*.mp4")) if vdir.exists() else []:
        rel = p.relative_to(root).as_posix()
        vids[rel] = {"path": rel, "label": p.stem, "line": p.parent.name, "exists": True}
    for sc in rt.scenarios.values():
        for cue in sc.cameras.values():
            for v in (cue.video, cue.then):
                if v and v not in vids:
                    vids[v] = {"path": v, "label": f"{Path(v).stem}（未備齊，合成畫面）",
                               "line": Path(v).parent.name, "exists": False}
    for cam in rt.plant.cameras.values():
        if cam.default_video and cam.default_video not in vids:
            vids[cam.default_video] = {"path": cam.default_video, "label": f"{Path(cam.default_video).stem}（未備齊，合成畫面）",
                                       "line": cam.line, "exists": False}
    return list(vids.values())


@router.get("/demo/settings")
def demo_settings(request: Request) -> dict:
    rt = get_rt(request)
    esc = rt.settings.escalation
    return {"escalation_timeout_s": esc.demo_ack_timeout_s or esc.timeout_for("S2"), "jev_enabled": rt.jev.enabled,
            "jev_has_key": rt.jev.has_key, "llm_enabled": rt.llm.enabled, "notifier": rt.notifier.name,
            "demo_ack_timeout_s": esc.demo_ack_timeout_s, "bus": rt.bus.stats()}


@router.post("/demo/settings")
def update_demo_settings(body: DemoSettingsReq, request: Request) -> dict:
    """控制台：Jev 開關（展示退路）、升級逾時秒數（覆蓋 S1–S3 的預設）。"""
    rt = get_rt(request)
    if body.jev_enabled is not None:
        rt.jev.enabled = body.jev_enabled and rt.jev.has_key
    if body.llm_enabled is not None:
        rt.llm.enabled = body.llm_enabled
    timeout = body.escalation_timeout_s if body.escalation_timeout_s is not None else body.demo_ack_timeout_s
    if timeout is not None:
        rt.settings.escalation.demo_ack_timeout_s = timeout or None
    rt.audit.record("demo_settings_changed", **body.model_dump(exclude_none=True))
    return demo_settings(request)
