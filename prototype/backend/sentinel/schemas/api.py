"""API 請求格式（對齊 prototype/frontend/lib/api.ts）。person_id 可省略：改用 X-Person-Id／X-Role。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ActorReq(BaseModel):
    person_id: str | None = Field(default=None, description="操作人員 ID；省略時用 X-Person-Id／X-Role")


class CheckReq(ActorReq):
    checked: bool = True


class ReassignReq(ActorReq):
    to_person_id: str | None = None
    reason: str = "人工轉派"


class PermitReq(ActorReq):
    action: Literal["request", "approve", "reject"] = "request"


class ReportReq(ActorReq):
    text: str
    root_cause_confirmed: str | None = None
    root_cause: str | None = None          # 舊欄位名稱，相容用


class AgentReq(ActorReq):
    message: str
    wait: bool = False                     # True：同步等回覆（測試與 CLI 用）


class OverrideReq(ActorReq):
    target: Literal["incident", "workorder"]
    target_id: str
    field: Literal["final_severity", "severity", "assignee", "false_alarm", "safety_ack"]
    new: Any = None
    reason: str = Field(min_length=1)


class CameraSourceReq(BaseModel):
    video: str | None = None
    synthetic: str | None = None
    loop: bool = True


class DemoSettingsReq(BaseModel):
    jev_enabled: bool | None = None
    llm_enabled: bool | None = None
    escalation_timeout_s: float | None = None
    demo_ack_timeout_s: float | None = None   # 舊欄位名稱，相容用
