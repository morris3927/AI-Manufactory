"""工單狀態機（WO-1；08_系統設計.md §5.8，2026-10-21 依作業許可（permit-to-work）順序修訂）。

設備類：
OPEN ─指派─► ASSIGNED ─接單─► ACKED ─申請許可─► PERMIT_REQUESTED ─班長核准─► PERMIT_APPROVED
  │             │ 逾時未接                          │ 班長退回 → ACKED
  │             └► ESCALATED（重新指派上一級，回到 ASSIGNED）
  │                                                                            ▼ 技術員勾完 LOTO 檢查表
  └─主管標記誤報─► CANCELLED                                                LOTO_DONE ─► IN_REPAIR
                                                                 PENDING_VERIFY（Checklist 勾完）◄┘
                                                                     │ 回報通過 J5
                                                                     ▼
                                                                   CLOSED
為什麼許可在上鎖之前：設施規則第57條（116-01-01 施行版）要求作業前訂定危害防止計畫並落實作業許可程序；
一般 permit-to-work 先核發許可（列出隔離點），再依許可執行隔離與上鎖。
LOTO 最後一步由技術員勾選後，同一個人的操作連續記錄 LOTO_DONE → IN_REPAIR 兩筆轉換。
config.yaml workflow.require_permit=false 時退回舊流程（ACKED 直接勾 LOTO）。

安全類工單（PPE、疑似火災）：OPEN → ASSIGNED → ACKED → CLOSED，不需要 LOTO 與許可。

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
    PERMIT_REQUESTED = "PERMIT_REQUESTED"
    PERMIT_APPROVED = "PERMIT_APPROVED"
    LOTO_DONE = "LOTO_DONE"
    IN_REPAIR = "IN_REPAIR"
    PENDING_VERIFY = "PENDING_VERIFY"
    CLOSED = "CLOSED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


S = WoStatus
TERMINAL = {S.CLOSED, S.CANCELLED}
PRE_PERMIT = {S.OPEN, S.ASSIGNED, S.ESCALATED, S.ACKED, S.PERMIT_REQUESTED}
PRE_LOTO = PRE_PERMIT | {S.PERMIT_APPROVED}
PRE_REPAIR = PRE_LOTO | {S.LOTO_DONE}          # 維修 Agent 只有在 IN_REPAIR 之後才給檢查／維修步驟


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
    (S.ACKED, S.PERMIT_REQUESTED): Rule("human", EQUIP, label="申請作業許可"),
    (S.PERMIT_REQUESTED, S.PERMIT_APPROVED): Rule("human", EQUIP, min_role="leader", label="作業許可核准"),
    (S.PERMIT_REQUESTED, S.ACKED): Rule("human", EQUIP, min_role="leader", label="作業許可退回"),
    (S.PERMIT_APPROVED, S.LOTO_DONE): Rule("human", EQUIP, label="LOTO 勾完"),
    (S.ACKED, S.LOTO_DONE): Rule("human", EQUIP, label="LOTO 勾完（不需許可的舊流程）"),
    (S.LOTO_DONE, S.IN_REPAIR): Rule("human", EQUIP, label="LOTO 完成，開始維修"),
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


NEXT_ACTION = {S.OPEN: "assign", S.ASSIGNED: "ack", S.ESCALATED: "ack", S.ACKED: "permit_request",
               S.PERMIT_REQUESTED: "permit_approve", S.PERMIT_APPROVED: "loto", S.LOTO_DONE: "loto",
               S.IN_REPAIR: "checklist", S.PENDING_VERIFY: "report", S.CLOSED: None, S.CANCELLED: None}


def next_action(status: str, category: str, require_permit: bool = True) -> str | None:
    """前端用：目前該做的下一個人工操作。"""
    st = WoStatus(status)
    if category == "safety":
        return {S.ASSIGNED: "ack", S.ESCALATED: "ack", S.ACKED: "report"}.get(st)
    if st == S.ACKED and not require_permit:
        return "loto"
    return NEXT_ACTION.get(st)


class WorkorderService:
    def __init__(self, *, db: Database, bus: EventBus, clock: Clock, audit: AuditLogger, require_permit: bool = True):
        self.db, self.bus, self.clock, self.audit = db, bus, clock, audit
        self.require_permit = require_permit

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
            if (frm, to) == (S.ACKED, S.LOTO_DONE) and self.require_permit:
                raise TransitionError("必須先申請並由班長核准作業許可，才能開始上鎖（設施規則第57條作業許可程序）")
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
        allowed = {S.PERMIT_APPROVED} if self.require_permit else {S.ACKED, S.PERMIT_APPROVED}
        if wo.status not in allowed:
            if wo.status in (S.ACKED, S.PERMIT_REQUESTED) and self.require_permit:
                raise TransitionError("作業許可尚未核准：請先申請作業許可，由班長核准後才能開始上鎖（設施規則第57條）")
            raise TransitionError(f"目前狀態（{wo.status}）不能勾選 LOTO")
        if step_no not in steps:
            raise TransitionError(f"LOTO {loto_id} 沒有第 {step_no} 步")
        if step_no not in self.loto_checked(wo_id):
            self.db.add(LotoRecord(workorder_id=wo_id, loto_id=loto_id, step_no=step_no, checked_by=person_id,
                                   ts=self.clock.now()))
            self.audit.record("loto_step_checked", actor=person_id, trace_id=self.incident(wo).trace_id,
                              workorder_id=wo_id, loto_id=loto_id, step_no=step_no)
        done = set(steps) <= self.loto_checked(wo_id)
        if done:
            await self.transition(wo_id, S.LOTO_DONE, actor=person_id, reason=f"{loto_id} 全部勾選完成")
            wo = await self.transition(wo_id, S.IN_REPAIR, actor=person_id, reason="LOTO 完成並驗證零能量，開始維修")
        else:
            await self.publish(wo, self.incident(wo), "loto_progress",
                               {"checked": sorted(self.loto_checked(wo_id)), "total": len(steps)})
        return wo, done

    async def permit(self, wo_id: str, person_id: str, action: str, *, loto_id: str | None = None,
                     isolation_points: list[str] | None = None) -> Workorder:
        """作業許可（permit-to-work）：request（技術員，ACKED → PERMIT_REQUESTED）、
        approve（班長以上，→ PERMIT_APPROVED）、reject（班長以上，退回 ACKED）。"""
        wo = self.get(wo_id)
        if wo.category not in EQUIP:
            raise TransitionError("安全類工單不需要作業許可")
        now = self.clock.now()
        trace = self.incident(wo).trace_id
        if action == "request":
            if wo.status != S.ACKED:
                raise TransitionError(f"作業許可要在接單後、上鎖前申請（目前 {wo.status}）")
            self._require_person(person_id)
            self.db.add(Permit(workorder_id=wo_id, requested_by=person_id, ts=now))
            self.audit.record("permit_requested", actor=person_id, trace_id=trace, workorder_id=wo_id,
                              loto_id=loto_id, isolation_points=isolation_points or [])
            return await self.transition(wo_id, S.PERMIT_REQUESTED, actor=person_id,
                                         reason=f"申請作業許可（隔離點依 {loto_id or 'LOTO 檢查表'}）")
        if wo.status != S.PERMIT_REQUESTED:
            raise TransitionError(f"沒有待核准的作業許可（目前 {wo.status}）")
        self._require_person(person_id, "leader")
        if action == "reject":
            self.audit.record("permit_rejected", actor=person_id, trace_id=trace, workorder_id=wo_id)
            return await self.transition(wo_id, S.ACKED, actor=person_id, reason="作業許可退回，請補充後重新申請")
        with self.db.session() as s:
            p = s.exec(select(Permit).where(Permit.workorder_id == wo_id, col(Permit.approved_by).is_(None))
                       .order_by(col(Permit.id).desc())).first()
            if p is None:
                p = Permit(workorder_id=wo_id, requested_by=wo.assignee_id or person_id, ts=now)
            p.approved_by = person_id
            s.add(p)
            s.commit()
        self.audit.record("permit_approved", actor=person_id, trace_id=trace, workorder_id=wo_id)
        return await self.transition(wo_id, S.PERMIT_APPROVED, actor=person_id, reason="作業許可核准，可以開始上鎖")

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
