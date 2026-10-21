"""SQLite 資料表（08_系統設計.md §6）。時間欄位一律存帶時區的 ISO 字串。"""
from __future__ import annotations

from datetime import date as Date, datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


class TzDateTime(TypeDecorator):
    """帶時區的 datetime ↔ ISO 字串（同一時區下字串排序即時間排序）。"""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.isoformat() if value is not None else None

    def process_result_value(self, value, dialect):
        return datetime.fromisoformat(value) if value else None


def ts_field(nullable: bool = False, **kw: Any) -> Any:
    return Field(default=None, sa_type=TzDateTime, nullable=nullable, **kw)


def json_field(default_factory=dict) -> Any:
    return Field(default_factory=default_factory, sa_type=JSON)


class Machine(SQLModel, table=True):
    id: str = Field(primary_key=True)
    line: str = Field(index=True)
    type: str
    name: str


class Person(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    line: str = Field(index=True)
    role: str                                   # operator／technician／leader／supervisor／manager
    discord_id: str | None = None


class ShiftAssignment(SQLModel, table=True):
    __tablename__ = "shift_assignment"
    id: int | None = Field(default=None, primary_key=True)
    date: Date = Field(index=True)
    shift: str                                  # day／night
    line: str = Field(index=True)
    person_id: str = Field(foreign_key="person.id")


class AccessRecord(SQLModel, table=True):
    __tablename__ = "access_record"
    id: int | None = Field(default=None, primary_key=True)
    person_id: str = Field(index=True)
    ts: datetime = ts_field()
    direction: str                              # in／out


class EventRow(SQLModel, table=True):
    __tablename__ = "event"
    event_id: str = Field(primary_key=True)
    ts: datetime = ts_field(index=True)
    topic: str = Field(index=True)
    source: str
    line: str | None = None
    machine_id: str | None = Field(default=None, index=True)
    camera_id: str | None = None
    type: str
    payload: dict = json_field()
    trace_id: str | None = Field(default=None, index=True)


class Incident(SQLModel, table=True):
    id: str = Field(primary_key=True)
    trace_id: str = Field(index=True)
    line: str
    machine_id: str | None = None
    camera_id: str | None = None
    type: str
    category: str                               # safety／equipment／quality
    rule_id: str
    rule_severity: str
    final_severity: str
    facts: dict = json_field()
    status: str = "open"                        # open／closed
    suspected_false_alarm: bool = False
    casualty: dict | None = Field(default=None, sa_type=JSON)   # {deaths, injured, hospitalized, set_by, set_at}；由人標記
    opened_at: datetime = ts_field()
    last_seen_at: datetime | None = ts_field(nullable=True)
    closed_at: datetime | None = ts_field(nullable=True)


class Workorder(SQLModel, table=True):
    id: str = Field(primary_key=True)
    incident_id: str = Field(index=True)
    status: str = "OPEN"
    category: str = "equipment"
    severity: str = "S2"
    assignee_id: str | None = None
    assignee_role: str | None = None
    cc: list = json_field(list)
    escalation_level: int = 0
    ack_deadline: datetime | None = ts_field(nullable=True)
    created_at: datetime = ts_field()
    acked_at: datetime | None = ts_field(nullable=True)
    closed_at: datetime | None = ts_field(nullable=True)
    report: dict | None = Field(default=None, sa_type=JSON)   # 結案回報 {text, root_cause_confirmed, submitted_by, submitted_at, check}


class WorkorderTransition(SQLModel, table=True):
    __tablename__ = "workorder_transition"
    id: int | None = Field(default=None, primary_key=True)
    workorder_id: str = Field(index=True)
    from_status: str = Field(sa_column_kwargs={"name": "from"})
    to_status: str = Field(sa_column_kwargs={"name": "to"})
    actor: str
    reason: str | None = None
    ts: datetime = ts_field()


class Notification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    workorder_id: str = Field(index=True)
    channel: str
    recipient_id: str | None = None
    discord_message_id: str | None = None
    kind: str = "dispatch"                      # dispatch／escalation／explanation／supervisor
    text: str = ""
    ts: datetime = ts_field()


class JevCall(SQLModel, table=True):
    __tablename__ = "jev_call"
    id: int | None = Field(default=None, primary_key=True)
    trace_id: str | None = Field(default=None, index=True)
    purpose: str
    questions_ver: str
    model: str | None = None
    latency_ms: float | None = None
    cost: float | None = None
    input_summary: dict = json_field()
    answers: dict = json_field()
    error: str | None = None
    ts: datetime = ts_field()


class LlmCall(SQLModel, table=True):
    __tablename__ = "llm_call"
    id: int | None = Field(default=None, primary_key=True)
    trace_id: str | None = Field(default=None, index=True)
    purpose: str
    model: str | None = None
    latency_ms: float | None = None
    tokens: int | None = None
    error: str | None = None
    ts: datetime = ts_field()


class Diagnosis(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    incident_id: str = Field(index=True)
    root_cause_top3: list = json_field(list)    # [{id, label, p}]
    confidence: float | None = None
    mode: str = "rules_only"                    # jev_top3／llm_deep／rules_only
    category: str | None = None
    p_false_alarm: float | None = None
    jev_severity: str | None = None
    sop_ids: list = json_field(list)
    explanation: str | None = None
    final_root_cause: str | None = None         # 結案時回填（校準曲線用）


class LotoRecord(SQLModel, table=True):
    __tablename__ = "loto_record"
    id: int | None = Field(default=None, primary_key=True)
    workorder_id: str = Field(index=True)
    loto_id: str
    step_no: int
    checked_by: str
    ts: datetime = ts_field()


class Permit(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    workorder_id: str = Field(index=True)
    requested_by: str
    approved_by: str | None = None
    ts: datetime = ts_field()


class ChecklistItem(SQLModel, table=True):
    __tablename__ = "checklist_item"
    id: int | None = Field(default=None, primary_key=True)
    workorder_id: str = Field(index=True)
    item: str
    checked_by: str
    ts: datetime = ts_field()


class AgentMessage(SQLModel, table=True):
    __tablename__ = "agent_message"
    id: int | None = Field(default=None, primary_key=True)
    workorder_id: str = Field(index=True)
    role: str                                   # user／assistant／tool
    content: str
    tool_calls: list = json_field(list)
    safety_flags: list = json_field(list)
    ts: datetime = ts_field()


class Override(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    target: str
    target_id: str
    field: str
    old: Any = Field(default=None, sa_type=JSON)
    new: Any = Field(default=None, sa_type=JSON)
    reason: str
    actor: str
    ts: datetime = ts_field()


class IncidentReport(SQLModel, table=True):
    __tablename__ = "incident_report"
    id: int | None = Field(default=None, primary_key=True)
    incident_id: str = Field(index=True)
    deadline: datetime = ts_field()
    draft: dict = json_field()                  # 職安法第37條通報欄位（模擬），由人確認後送出
    created_at: datetime | None = ts_field(nullable=True)
    submitted_by: str | None = None
    submitted_at: datetime | None = ts_field(nullable=True)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = ts_field(index=True)
    trace_id: str | None = Field(default=None, index=True)
    actor: str
    action: str
    detail: dict = json_field()
