"""J1–J3：文字事件判斷（一次請求問完）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from . import questions as Q
from .client import JevClient


class RootCause(BaseModel):
    id: str
    label: str
    p: float


class EventJudgement(BaseModel):
    category: str
    category_conf: float
    p_false_alarm: float
    severity_score: float                 # 0–2，0 = S1（Score 從 0 起算）
    jev_severity: str
    root_cause_top3: list[RootCause] = Field(default_factory=list)
    root_cause_conf: float = 0.0
    model: str | None = None
    cost: float | None = None
    latency_ms: float | None = None
    questions_ver: str = Q.EVENT_VER


def score_to_severity(score: float) -> str:
    return "S" + str(min(2, max(0, round(score))) + 1)


def build_state(incident: Any, machine: Any, timeline: list[str], similar_cases: list[Any]) -> dict:
    """只放判斷需要的欄位；數字先算好；不放人名與影像。"""
    return {
        "event": {
            "line": incident.line,
            "machine": getattr(machine, "name", None) or incident.machine_id or incident.camera_id,
            "machine_type": getattr(machine, "type", None),
            "source": incident.facts.get("source", "fused"),
            "type": incident.type,
            "rule_severity": incident.rule_severity,
            "facts": {k: v for k, v in incident.facts.items() if k != "source"},
        },
        "timeline_10min": timeline[-20:],
        "machine_context": {"last_maintenance_days_ago": getattr(machine, "days_since_pm", None)},
        "similar_cases": [getattr(c, "short", str(c)) for c in similar_cases[:5]],
    }


async def event(jev: JevClient, incident: Any, *, machine: Any, timeline: list[str], similar_cases: list[Any],
                failure_modes: dict[str, dict], severity_notes: list[str] | None = None) -> EventJudgement:
    """failure_modes：{id: {label, criteria, ...}}。"""
    criteria = {fid: fm.get("criteria") or fm.get("label") or fid for fid, fm in failure_modes.items()}
    res = await jev.ask(build_state(incident, machine, timeline, similar_cases), Q.event_questions(criteria, severity_notes),
                        purpose="judge.event", trace_id=incident.trace_id, questions_ver=Q.EVENT_VER)
    cat = res.choice("category")
    sev = res.score("severity")
    top3: list[RootCause] = []
    rc_conf = 0.0
    if "root_cause" in res.answers:
        rc = res.choice("root_cause")
        rc_conf = rc.confidence
        top3 = [RootCause(id=k, label="以上皆非（新的故障模式）" if k == Q.NEW_MODE else
                          (failure_modes.get(k) or {}).get("label", k), p=round(p, 4))
                for k, p in rc.top(3)]
    return EventJudgement(
        category=cat.choice, category_conf=cat.confidence, p_false_alarm=res.noul("is_false_alarm"),
        severity_score=sev.score, jev_severity=score_to_severity(sev.score), root_cause_top3=top3,
        root_cause_conf=rc_conf, model=res.model, cost=res.cost, latency_ms=res.latency_ms,
    )
