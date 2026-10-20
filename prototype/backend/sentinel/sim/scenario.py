"""劇本（data/simulated/scenarios/*.yaml）：影片＋Log 腳本＋預期結果。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class CameraCue(BaseModel):
    video: str | None = None
    then: str | None = None
    synthetic: str | None = None      # 影片不存在時的合成畫面樣式；未填則依檔名推測
    loop: bool = False
    jam_at_s: float | None = None     # 合成卡料畫面：幾秒後板材卡住


class SimDetection(BaseModel):
    """沒有 YOLO 權重時的替代偵測結果（依劇本時間重播）。"""

    camera: str
    t_from: float
    t_to: float
    detections: list[dict[str, Any]]


class ScriptedEvent(BaseModel):
    """劇本直接注入的事件（例如影像 person_in_zone）；MES 機台事件請寫在 mes_script 的 event 步驟。"""

    t: float
    topic: str = "vision.events"
    type: str
    camera_id: str | None = None
    machine_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ExpectIncident(BaseModel):
    type: str
    severity: str
    category: str | None = None
    within_s: float = 3


class Expect(BaseModel):
    t0: float = 0                     # 異常「發生」的劇本時間，within_s 從這裡算
    incident: ExpectIncident | None = None
    notify_within_s: float | None = None
    assignee_role: str | None = None
    llm_called: bool | None = None


class Scenario(BaseModel):
    id: str
    title: str
    duration_s: float = 30
    cameras: dict[str, CameraCue] = Field(default_factory=dict)
    mes_script: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    sim_detections: list[SimDetection] = Field(default_factory=list)
    events: list[ScriptedEvent] = Field(default_factory=list)
    expect: Expect = Field(default_factory=Expect)


def load_scenarios(directory: Path) -> dict[str, Scenario]:
    out: dict[str, Scenario] = {}
    if not directory.exists():
        return out
    for p in sorted(directory.glob("*.yaml")):
        sc = Scenario(**yaml.safe_load(p.read_text(encoding="utf-8")))
        out[sc.id] = sc
    return out
