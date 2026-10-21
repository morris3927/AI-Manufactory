"""班表與門禁查詢（IN-3、IN-4）。"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlmodel import col, select

from ..db.models import AccessRecord, Person, ShiftAssignment
from ..db.session import Database

log = logging.getLogger(__name__)

ROLE_RANK = {"operator": 0, "technician": 1, "leader": 2, "supervisor": 3, "manager": 4}
PLANT_LINE = "plant"         # 全廠共用的職位（職安人員、急救人員）在 roster.csv 用 line=plant
ROLE_LABEL = {"safety_officer": "職安人員", "first_aider": "急救人員",
              "operator": "作業員", "technician": "技術員", "leader": "班長", "supervisor": "組長",
              "manager": "課長"}


def shift_of(ts: datetime) -> str:
    return "day" if 8 <= ts.hour < 20 else "night"


class Roster:
    def __init__(self, db: Database):
        self.db = db

    def on_shift(self, line: str, at: datetime) -> list[Person]:
        """當班人員。班表沒有今天的資料時沿用最近一天（Demo 日期不固定）。"""
        day, shift = at.date(), shift_of(at)
        rows = self._assignments(line, day, shift)
        if not rows:
            latest = self.db.first(select(ShiftAssignment.date).where(ShiftAssignment.line == line)
                                   .order_by(col(ShiftAssignment.date).desc()))
            if latest:
                rows = self._assignments(line, latest, shift) or self._assignments(line, latest, None)
                if rows:
                    log.debug("班表無 %s %s 資料，沿用 %s", day, shift, latest)
        ids = [r.person_id for r in rows]
        if not ids:
            return []
        people = self.db.all(select(Person).where(col(Person.id).in_(ids)))
        return sorted(people, key=lambda p: (ROLE_RANK.get(p.role, 0), p.id))

    def plant_role(self, role: str, at: datetime) -> list[Person]:
        """全廠職位（safety_officer／first_aider）的當班人員。"""
        return [p for p in self.on_shift(PLANT_LINE, at) if p.role == role]

    def _assignments(self, line: str, day: date, shift: str | None) -> list[ShiftAssignment]:
        stmt = select(ShiftAssignment).where(ShiftAssignment.line == line, ShiftAssignment.date == day)
        if shift:
            stmt = stmt.where(ShiftAssignment.shift == shift)
        return self.db.all(stmt)

    def in_plant(self, person_id: str, at: datetime) -> bool:
        """門禁最後一筆是 in 才算在廠；沒有門禁資料時視為在廠（M1 不強制門禁）。"""
        last = None
        for r in self.db.all(select(AccessRecord).where(AccessRecord.person_id == person_id)
                             .order_by(col(AccessRecord.ts))):
            if r.ts <= at:
                last = r
        return last is None or last.direction == "in"

    def person(self, person_id: str) -> Person | None:
        return self.db.get(Person, person_id)
