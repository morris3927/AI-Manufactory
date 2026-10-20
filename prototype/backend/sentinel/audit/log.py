"""稽核紀錄（GV-2）：只新增、不修改。資料庫層另有 trigger 擋住 UPDATE／DELETE。"""
from __future__ import annotations

from typing import Any

from sqlmodel import select

from ..clock import Clock
from ..db.models import AuditLog
from ..db.session import Database
from ..ids import jsonable


class AuditLogger:
    def __init__(self, db: Database, clock: Clock):
        self.db = db
        self.clock = clock

    def record(self, action: str, *, actor: str = "system", trace_id: str | None = None,
               **detail: Any) -> None:
        self.db.add(AuditLog(ts=self.clock.now(), trace_id=trace_id, actor=actor, action=action,
                             detail=jsonable(detail)))

    def timeline(self, trace_id: str | None = None, limit: int = 500) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.ts, AuditLog.id).limit(limit)
        if trace_id:
            stmt = stmt.where(AuditLog.trace_id == trace_id)
        return self.db.all(stmt)
