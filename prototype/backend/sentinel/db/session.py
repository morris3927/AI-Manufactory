"""資料庫連線、建表與事件寫入。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from ..ids import jsonable
from ..schemas.events import Event
from . import models  # noqa: F401  註冊所有資料表
from .models import EventRow

# audit_log 只能新增：用 SQLite trigger 在資料庫層擋住 UPDATE／DELETE
_AUDIT_TRIGGERS = [
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;",
]


class Database:
    def __init__(self, path: Path | str):
        self.url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{path}"
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if self.url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(self.url, **kwargs)

        @sa_event.listens_for(self.engine, "connect")
        def _pragma(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    def create_all(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        self._add_missing_columns()
        with self.engine.begin() as conn:
            for stmt in _AUDIT_TRIGGERS:
                conn.execute(text(stmt))

    def _add_missing_columns(self) -> None:
        """舊的 sentinel.db 缺新欄位時補上（只加可為空的欄位，不改既有資料）；改型別請 make reset。"""
        from sqlalchemy import inspect

        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in SQLModel.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col_ in table.columns:
                    if col_.name not in have and col_.nullable:
                        conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col_.name}" '
                                          f'{col_.type.compile(dialect=self.engine.dialect)}'))

    def reset(self) -> None:
        """重置 Demo：整個重建（DROP TABLE 不受 audit trigger 限制）。"""
        SQLModel.metadata.drop_all(self.engine)
        self.create_all()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as s:
            yield s

    def add(self, *rows) -> None:
        with self.session() as s:
            for r in rows:
                s.add(r)
            s.commit()

    def get(self, model, key):
        with self.session() as s:
            return s.get(model, key)

    def all(self, stmt):
        with self.session() as s:
            return list(s.exec(stmt).all())

    def first(self, stmt):
        with self.session() as s:
            return s.exec(stmt).first()

    def persist_event(self, evt: Event) -> None:
        self.add(EventRow(
            event_id=evt.event_id, ts=evt.ts, topic=str(evt.topic), source=evt.source, line=evt.line,
            machine_id=evt.machine_id, camera_id=evt.camera_id, type=evt.type,
            payload=jsonable(evt.payload), trace_id=evt.trace_id,
        ))


__all__ = ["Database", "select"]
