"""工單狀態機（WO-1；08_系統設計.md §5.8）。

OPEN ──指派──► ASSIGNED ──接單──► ACKED ──LOTO 勾完──► LOTO_DONE ──許可核准──► IN_REPAIR
  │               │ 逾時未接                                                    │
  │               └──► ESCALATED（重新指派上一級，回到 ASSIGNED）                ▼
  │                                                              PENDING_VERIFY（Checklist 勾完）
  └──主管標記誤報──► CANCELLED                                                   │ 回報通過 J5
                                                                                 ▼
                                                                               CLOSED
安全類工單（PPE）：OPEN → ASSIGNED → ACKED → CLOSED，不需要 LOTO。

- 每次轉換寫入 workorder_transition（誰、何時、從哪到哪、原因）＋ 稽核紀錄，並發布 tasks。
- 維修 Agent（actor 以 agent 開頭）與 AI（jev／llm）一律不能改變工單狀態。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlmodel import col, select

from ..audit.log import AuditLogger
from ..bus import EventBus
from ..clock import Clock
from ..db.models import ChecklistItem, Incident, LotoRecord, Permit, Person, Workorder, WorkorderTransition
from ..db.session import Database
from ..schemas.events import Event, Topic
from ..sim.roster import ROLE_RANK


class WoStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    ACKED = "ACKED"
    LOTO_DONE = "LOTO_DONE"
    IN_REPAIR = "IN_REPAIR"
    PENDING_VERIFY = "PENDING_VERIFY"
    CLOSED = "CLOSED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


S = WoStatus
TERMINAL = {S.CLOSED, S.CANCELLED}
PRE_LOTO = {S.OPEN, S.ASSIGNED, S.ESCALATED, S.ACKED}


@dataclass(frozen=True)
class Rule:
    actor: str                  # system／human／any
    categories: frozenset[str] | None = None   # None → 不限類別
    min_role: str | None = None  # 人員最低職位
    label: str = ""


EQUIP = frozenset({"equipment", "quality"})
SAFETY = frozenset({"safety"})

TRANSITIONS: dict[tuple[WoStatus, WoStatus], Rule] = {
    (S.OPEN, S.ASSIGNED): Rule("any", label="指派"),
    (S.OPEN, S.CANCELLED): Rule("human", min_role="leader", label="標記誤報"),
    (S.ASSIGNED, S.ASSIGNED): Rule("any", label="轉派"),
    (S.ASSIGNED, S.ACKED): Rule("human", label="接單"),
    (S.ASSIGNED, S.ESCALATED): Rule("system", label="逾時升級"),
    (S.ASSIGNED, S.CANCELLED): Rule("human", min_role="leader", label="標記誤報"),
    (S.ESCALATED, S.ASSIGNED): Rule("system", label="重新指派上一級"),
    (S.ACKED, S.LOTO_DONE): Rule("human", EQUIP, label="LOTO 勾完"),
    (S.LOTO_DONE, S.IN_REPAIR): Rule("human", EQUIP, min_role="leader", label="許可核准"),
    (S.IN_REPAIR, S.PENDING_VERIFY): Rule("human", EQUIP, label="Checklist 勾完"),
    (S.PENDING_VERIFY, S.CLOSED): Rule("human", EQUIP, label="回報通過"),
    (S.ACKED, S.CLOSED): Rule("human", SAFETY, label="安全類結案"),
}


class TransitionError(Exception):
    """狀態轉換不合法（API 回 409）。"""


class PermissionDenied(Exception):
    """角色或身分不允許（API 回 403）。"""


def actor_kind(actor: str) -> str:
    a = actor.lower()
    if a.startswith("agent") or a in {"jev", "llm"}:
        return "ai"
    return "system" if a == "system" else "human"


def can_transition(frm: str, to: str, category: str, actor: str = "system") -> bool:
    try:
        rule = TRANSITIONS[(WoStatus(frm), WoStatus(to))]
    except (KeyError, ValueError):
        return False
    kind = actor_kind(actor)
    if kind == "ai":
        return False
    if rule.actor != "any" and rule.actor != kind:
        return False
    return rule.categories is None or category in rule.categories


class WorkorderService:
    def __init__(self, *, db: Database, bus: EventBus, clock: Clock, audit: AuditLogger):
        self.db, self.bus, self.clock, self.audit = db, bus, clock, audit

    # ── 查詢 ───────────────────────────────────────────────────
    def get(self, wo_id: str) -> Workorder:
        wo = self.db.get(Workorder, wo_id)
        if wo is None:
            raise KeyError(wo_id)
        return wo

    def incident(self, wo: Workorder) -> Incident:
        inc = self.db.get(Incident, wo.incident_id)
        assert inc is not None
        return inc

    def for_incident(self, incident_id: str) -> Workorder | None:
        return self.db.first(select(Workorder).where(Workorder.incident_id == incident_id))

    def transitions(self, wo_id: str) -> list[WorkorderTransition]:
        return self.db.all(select(WorkorderTransition).where(WorkorderTransition.workorder_id == wo_id)
                           .order_by(col(WorkorderTransition.id)))

    def loto_checked(self, wo_id: str) -> set[int]:
        return {r.step_no for r in self.db.all(select(LotoRecord).where(LotoRecord.workorder_id == wo_id))}

    def checklist_checked(self, wo_id: str) -> set[str]:
        return {r.item for r in self.db.all(select(ChecklistItem).where(ChecklistItem.workorder_id == wo_id))}

    def _require_person(self, person_id: str, min_role: str | None = None) -> Person:
        p = self.db.get(Person, person_id)
        if p is None:
            raise PermissionDenied(f"找不到人員 {person_id}")
        if min_role and ROLE_RANK.get(p.role, 0) < ROLE_RANK[min_role]:
            raise PermissionDenied(f"{p.name}（{p.role}）權限不足，需要 {min_role} 以上")
        return p

    # ── 建立與轉換 ─────────────────────────────────────────────
    async def create(self, incident: Incident, *, severity: str, category: str) -> Workorder:
        now = self.clock.now()
        with self.db.session() as s:
            n = len(s.exec(select(Workorder.id)).all())
        wo = Workorder(id=f"WO-{n + 1:04d}", incident_id=incident.id, status=S.OPEN, category=category,
                       severity=severity, created_at=now)
        self.db.add(wo, WorkorderTransition(workorder_id=wo.id, from_status="", to_status=S.OPEN, actor="system",
                                            reason="規則命中建單", ts=now))
        self.audit.record("workorder_created", trace_id=incident.trace_id, workorder_id=wo.id,
                          incident_id=incident.id, severity=severity, category=category)
        await self.publish(wo, incident, "workorder_created", {})
        return wo

    async def transition(self, wo_id: str, to: WoStatus | str, *, actor: str, reason: str | None = None,
                         **updates: Any) -> Workorder:
        to = WoStatus(to)
        kind = actor_kind(actor)
        if kind == "ai":
            raise PermissionDenied("AI／維修 Agent 不能改變工單狀態；狀態只能由人在介面上操作")
        with self.db.session() as s:
            wo = s.get(Workorder, wo_id)
            if wo is None:
                raise KeyError(wo_id)
            frm = WoStatus(wo.status)
            rule = TRANSITIONS.get((frm, to))
            if rule is None:
                raise TransitionError(f"{wo_id}：不允許 {frm} → {to}")
            if rule.categories is not None and wo.category not in rule.categories:
                raise TransitionError(f"{wo_id}：{wo.category} 類工單不允許 {frm} → {to}")
            if rule.actor != "any" and rule.actor != kind:
                raise PermissionDenied(f"{frm} → {to} 只能由 {'人員' if rule.actor == 'human' else '系統'} 執行")
            if kind == "human" and rule.min_role:
                self._require_person(actor, rule.min_role)
            now = self.clock.now()
            wo.status = to
            for k, v in updates.items():
                setattr(wo, k, v)
            if to == S.ACKED:
                wo.acked_at = now
            if to in TERMINAL:
                wo.closed_at = now
                inc = s.get(Incident, wo.incident_id)
                if inc:
                    inc.status = "closed"
                    inc.closed_at = now
                    s.add(inc)
            s.add(wo)
            s.add(WorkorderTransition(workorder_id=wo_id, from_status=frm, to_status=to, actor=actor,
                                      reason=reason or rule.label, ts=now))
            s.commit()
            s.refresh(wo)
        inc = self.incident(wo)
        self.audit.record("workorder_transition", actor=actor, trace_id=inc.trace_id, workorder_id=wo_id,
                          **{"from": frm, "to": to, "reason": reason or rule.label})
        await self.publish(wo, inc, "workorder_transition", {"from": frm, "to": to, "actor": actor,
                                                              "reason": reason or rule.label})
        return wo

    async def assign(self, wo_id: str, *, person_id: str | None, role: str | None, ack_timeout_s: float,
                     actor: str = "system", reason: str | None = None, cc: list[str] | None = None,
                     escalation_level: int | None = None) -> Workorder:
        updates: dict[str, Any] = {"assignee_id": person_id, "assignee_role": role,
                                   "ack_deadline": self.clock.now() + timedelta(seconds=ack_timeout_s)}
        if cc is not None:
            updates["cc"] = cc
        if escalation_level is not None:
            updates["escalation_level"] = escalation_level
        return await self.transition(wo_id, S.ASSIGNED, actor=actor, reason=reason, **updates)

    async def ack(self, wo_id: str, person_id: str) -> Workorder:
        p = self._require_person(person_id)
        return await self.transition(wo_id, S.ACKED, actor=person_id, reason=f"{p.name} 接單",
                                     assignee_id=p.id, assignee_role=p.role, ack_deadline=None)

    async def check_loto(self, wo_id: str, step_no: int, person_id: str, *, loto_id: str,
                         steps: list[int]) -> tuple[Workorder, bool]:
        wo = self.get(wo_id)
        self._require_person(person_id)
        if wo.category not in EQUIP:
            raise TransitionError("安全類工單不需要 LOTO")
        if wo.status != S.ACKED:
            raise TransitionError(f"LOTO 只能在接單後勾選（目前 {wo.status}）")
        if step_no not in steps:
            raise TransitionError(f"LOTO {loto_id} 沒有第 {step_no} 步")
        if step_no not in self.loto_checked(wo_id):
            self.db.add(LotoRecord(workorder_id=wo_id, loto_id=loto_id, step_no=step_no, checked_by=person_id,
                                   ts=self.clock.now()))
            self.audit.record("loto_step_checked", actor=person_id, trace_id=self.incident(wo).trace_id,
                              workorder_id=wo_id, loto_id=loto_id, step_no=step_no)
        done = set(steps) <= self.loto_checked(wo_id)
        if done:
            wo = await self.transition(wo_id, S.LOTO_DONE, actor=person_id, reason=f"{loto_id} 全部勾選完成")
        else:
            await self.publish(wo, self.incident(wo), "loto_progress",
                                {"checked": sorted(self.loto_checked(wo_id)), "total": len(steps)})
        return wo, done

    async def permit(self, wo_id: str, person_id: str, action: str) -> Workorder:
        wo = self.get(wo_id)
        if wo.status != S.LOTO_DONE:
            raise TransitionError(f"作業許可只能在 LOTO 完成後申請／核准（目前 {wo.status}）")
        now = self.clock.now()
        if action == "request":
            self._require_person(person_id)
            self.db.add(Permit(workorder_id=wo_id, requested_by=person_id, ts=now))
            self.audit.record("permit_requested", actor=person_id, trace_id=self.incident(wo).trace_id,
                              workorder_id=wo_id)
            await self.publish(wo, self.incident(wo), "permit_requested", {"by": person_id})
            return wo
        self._require_person(person_id, "leader")
        with self.db.session() as s:
            p = s.exec(select(Permit).where(Permit.workorder_id == wo_id, col(Permit.approved_by).is_(None))).first()
            if p is None:
                p = Permit(workorder_id=wo_id, requested_by=wo.assignee_id or person_id, ts=now)
            p.approved_by = person_id
            s.add(p)
            s.commit()
        return await self.transition(wo_id, S.IN_REPAIR, actor=person_id, reason="作業許可核准")

    async def check_item(self, wo_id: str, item: str, person_id: str, items: list[str]) -> tuple[Workorder, bool]:
        wo = self.get(wo_id)
        self._require_person(person_id)
        if wo.status != S.IN_REPAIR:
            raise TransitionError(f"Checklist 只能在維修中勾選（目前 {wo.status}）")
        if item not in items:
            raise TransitionError(f"Checklist 沒有「{item}」")
        if item not in self.checklist_checked(wo_id):
            self.db.add(ChecklistItem(workorder_id=wo_id, item=item, checked_by=person_id, ts=self.clock.now()))
            self.audit.record("checklist_checked", actor=person_id, trace_id=self.incident(wo).trace_id,
                              workorder_id=wo_id, item=item)
        done = set(items) <= self.checklist_checked(wo_id)
        if done:
            wo = await self.transition(wo_id, S.PENDING_VERIFY, actor=person_id, reason="Checklist 全部完成")
        return wo, done

    async def publish(self, wo: Workorder, inc: Incident, type_: str, extra: dict) -> None:
        await self.bus.publish(Event(
            ts=self.clock.now(), topic=Topic.TASKS, source="dispatch", line=inc.line, machine_id=inc.machine_id,
            camera_id=inc.camera_id, type=type_, trace_id=inc.trace_id,
            payload={"workorder_id": wo.id, "incident_id": inc.id, "status": wo.status, "severity": wo.severity,
                     "category": wo.category, "assignee_id": wo.assignee_id, "assignee_role": wo.assignee_role,
                     "escalation_level": wo.escalation_level,
                     "ack_deadline": wo.ack_deadline.isoformat() if isinstance(wo.ack_deadline, datetime) else None,
                     **extra}))
