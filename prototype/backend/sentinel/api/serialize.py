"""API 回應格式（對齊 prototype/frontend/lib/types.ts）。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from ..agent.repair_agent import message_dict
from ..db.models import AgentMessage, ChecklistItem, Diagnosis, Incident, LotoRecord, Permit, Workorder
from ..rules.engine import TYPE_LABEL
from ..jev.questions import EVENT_VER
from ..llm.explain import FALLBACK

if TYPE_CHECKING:
    from ..runtime import Runtime


def iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def title_of(rt: "Runtime", inc: Incident) -> str:
    m = rt.plant.machines.get(inc.machine_id or "")
    where = f"{m.id} {m.name}" if m else (inc.camera_id or "")
    return f"{where}：{TYPE_LABEL.get(inc.type, inc.type)}"


def diagnosis_out(d: Diagnosis | None, inc: Incident) -> dict | None:
    if d is None:
        return None
    if d.explanation is None:
        status = None if inc.category == "safety" else "pending"
    else:
        status = "failed" if d.explanation == FALLBACK else "done"
    return {"incident_id": d.incident_id, "mode": d.mode, "confidence": d.confidence,
            "root_cause_top3": [{"failure_mode_id": r.get("id"), "name": r.get("label", r.get("id")), "p": r.get("p")}
                                for r in d.root_cause_top3 or []],
            "explanation": d.explanation, "explanation_status": status, "p_false_alarm": d.p_false_alarm,
            "questions_ver": EVENT_VER if d.mode != "rules_only" else None, "category": d.category,
            "jev_severity": d.jev_severity, "sop_ids": d.sop_ids, "final_root_cause": d.final_root_cause}


def incident_out(rt: "Runtime", inc: Incident, base_url: str = "", with_diag: bool = True) -> dict:
    box = (inc.facts or {}).get("jam_box")
    out = inc.model_dump(mode="json") | {
        "title": title_of(rt, inc), "jev_box": box if isinstance(box, list) and len(box) == 4 else None,
        "keyframe_url": f"{base_url}api/incidents/{inc.id}/keyframe" if rt.keyframe_path(inc.id).exists() else None,
    }
    if with_diag:
        out["diagnosis"] = diagnosis_out(rt.dispatch.diagnosis_for(inc.id), inc)
    return out


def workorder_out(rt: "Runtime", wo: Workorder, inc: Incident | None = None) -> dict:
    inc = inc or rt.db.get(Incident, wo.incident_id)
    p = rt.roster.person(wo.assignee_id) if wo.assignee_id else None
    out = wo.model_dump(mode="json", exclude={"report"})
    if inc:
        out |= {"line": inc.line, "machine_id": inc.machine_id, "camera_id": inc.camera_id, "type": inc.type,
                "title": title_of(rt, inc), "trace_id": inc.trace_id,
                "suspected_false_alarm": inc.suspected_false_alarm}
    out["assignee_name"] = p.name if p else None
    return out


def _sop_steps(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines[1:] if ln] if len(lines) > 1 else [text]


def workorder_detail(rt: "Runtime", wo_id: str, base_url: str = "") -> dict:
    wo = rt.workorders.get(wo_id)
    inc = rt.workorders.incident(wo)
    diag = rt.dispatch.diagnosis_for(inc.id)
    th = rt.settings.thresholds
    sop_plan = rt.kb.plan_sops(diag.root_cause_top3 if diag else [], top1_thr=th.sop_top1, low_thr=th.sop_low)
    sops = [{"sop_id": c.sop_id, "title": c.title, "phase": c.phase,
             "failure_mode_id": (c.failure_mode_ids or [None])[0], "steps": _sop_steps(c.text), "source": "internal"}
            for c in sop_plan["chunks"]]

    loto = rt.agent.ctx.loto_for(wo_id) if wo.category != "safety" else None
    loto_out = None
    if loto:
        recs = {r.step_no: r for r in rt.db.all(select(LotoRecord).where(LotoRecord.workorder_id == wo_id))}
        loto_out = {"loto_id": loto.loto_id, "machine_type": loto.machine_type if isinstance(loto.machine_type, str)
                    else ",".join(loto.machine_type), "title": loto.loto_id, "permit_required": loto.permit_required,
                    "is_default": loto.is_default, "release": loto.release,
                    "energy_sources": loto.energy_sources,
                    "steps": [{"step_no": s.no, "text": s.action, "check": s.check,
                               "checked_by": recs[s.no].checked_by if s.no in recs else None,
                               "ts": iso(recs[s.no].ts) if s.no in recs else None} for s in loto.steps]}

    permits = rt.db.all(select(Permit).where(Permit.workorder_id == wo_id).order_by(col(Permit.id)))
    permit = None
    if permits:
        last = permits[-1]
        permit = {"requested_by": last.requested_by, "requested_at": iso(last.ts), "approved_by": last.approved_by,
                  "ts": iso(last.ts)}

    top1 = (diag.root_cause_top3 or [{}])[0].get("id") if diag else None
    items = rt.kb.checklist_for(top1) if wo.category != "safety" else []
    done = {r.item: r for r in rt.db.all(select(ChecklistItem).where(ChecklistItem.workorder_id == wo_id))}
    checklist = [{"item": str(i), "label": it, "checked_by": done[it].checked_by if it in done else None,
                  "ts": iso(done[it].ts) if it in done else None} for i, it in enumerate(items)]

    msgs = rt.db.all(select(AgentMessage).where(AgentMessage.workorder_id == wo_id,
                                                col(AgentMessage.role).in_(["user", "assistant"]))
                     .order_by(col(AgentMessage.id)))
    transitions = [{"from": t.from_status or None, "to": t.to_status, "actor": t.actor, "reason": t.reason,
                    "ts": iso(t.ts)} for t in rt.workorders.transitions(wo_id)]
    return workorder_out(rt, wo, inc) | {
        "incident": incident_out(rt, inc, base_url), "diagnosis": diagnosis_out(diag, inc),
        "sops": sops, "sop_mode": sop_plan["mode"], "sop_note": sop_plan.get("note"),
        "loto": loto_out, "permit": permit, "checklist": checklist,
        "agent_messages": [message_dict(m) for m in msgs], "transitions": transitions, "report": wo.report,
    }
