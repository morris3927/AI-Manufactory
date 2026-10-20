"""逐級升級（RU-5）：ack_timeout 內沒人接單 → 升到下一個職位並重新通知。

技術員 → 班長 → 組長 → 課長。逾時秒數：S2 5 分、S3 2 分（config.yaml；Demo 可調成 30 秒）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlmodel import select

from ..clock import Clock
from ..config import Settings
from ..db.models import Workorder
from ..db.session import Database
from ..sim.roster import ROLE_LABEL, Roster
from .routing import ESCALATION_LADDER, next_role, pick

if TYPE_CHECKING:
    from ..dispatch.service import DispatchService
    from ..dispatch.workorder import WorkorderService

log = logging.getLogger(__name__)


class EscalationService:
    def __init__(self, *, db: Database, clock: Clock, settings: Settings, roster: Roster,
                 workorders: "WorkorderService", dispatch: "DispatchService"):
        self.db, self.clock, self.settings, self.roster = db, clock, settings, roster
        self.workorders, self.dispatch = workorders, dispatch

    async def check(self) -> list[str]:
        """檢查所有已指派但逾時未接單的工單；回傳已升級的工單 ID。"""
        now = self.clock.now()
        overdue = [wo for wo in self.db.all(select(Workorder).where(Workorder.status == "ASSIGNED"))
                   if wo.ack_deadline is not None and wo.ack_deadline <= now]
        done: list[str] = []
        for wo in overdue:
            if await self.escalate(wo):
                done.append(wo.id)
        return done

    async def escalate(self, wo: Workorder) -> bool:
        inc = self.workorders.incident(wo)
        now = self.clock.now()
        role = wo.assignee_role if wo.assignee_role in ESCALATION_LADDER else "technician"
        target = next_role(role)
        notes: list[str] = []
        person = pick(self.roster, self.roster.on_shift(inc.line, now), target, now, notes) if target else None
        if person is None:
            log.warning("%s 已到最高職位，無法再升級", wo.id)
            await self.workorders.transition(wo.id, "ASSIGNED", actor="system", reason="已到最高職位，延長接單時限",
                                             ack_deadline=None)
            return False
        await self.workorders.transition(wo.id, "ESCALATED", actor="system",
                                         reason=f"逾時未接單（{ROLE_LABEL.get(role, role)}）")
        cc = sorted(set((wo.cc or []) + ([wo.assignee_id] if wo.assignee_id else [])))
        wo = await self.workorders.assign(
            wo.id, person_id=person.id, role=person.role, cc=cc, escalation_level=wo.escalation_level + 1,
            ack_timeout_s=self.settings.escalation.timeout_for(inc.final_severity),
            reason=f"升級至{ROLE_LABEL.get(person.role, person.role)} {person.name}" + ("；" + "；".join(notes) if notes else ""))
        await self.dispatch.notify(wo, inc, self.dispatch.diagnosis_for(inc.id), None, kind="escalation")
        return True
