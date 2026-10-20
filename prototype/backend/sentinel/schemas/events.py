"""事件共通格式（08_系統設計.md §4.2）。"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..ids import new_id


class Topic(StrEnum):
    VISION = "vision.events"
    MES = "mes.logs"
    ACCESS = "access.events"
    ALERTS = "alerts"
    TASKS = "tasks"
    AI = "ai.results"
    FRAMES = "frames"          # 只在程式內傳遞，不寫入資料庫


class Event(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_id: str = ""
    ts: datetime
    topic: str
    source: str
    line: str | None = None
    machine_id: str | None = None
    camera_id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.event_id:
            self.event_id = new_id(self.ts)

    @property
    def entity(self) -> str | None:
        """規則視窗的主體：有機台就用機台，否則用攝影機（例如組裝線 PPE）。"""
        return self.machine_id or self.camera_id
