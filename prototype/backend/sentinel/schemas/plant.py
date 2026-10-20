"""工廠設定（data/simulated/plant.yaml）。"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Machine(BaseModel):
    id: str
    type: str
    name: str
    line: str = ""
    days_since_pm: int | None = None


class MotionCfg(BaseModel):
    roi: str = "feed_inlet"
    on: float = 0.4          # EMA（變化像素 %）超過 → running
    off: float = 0.1         # EMA 低於 → idle（遲滯，避免來回跳）
    alpha: float = 0.3


class Camera(BaseModel):
    id: str
    machine_id: str | None = None
    line: str = ""
    rois: dict[str, list[int]] = Field(default_factory=dict)
    detectors: list[str] = Field(default_factory=list)
    motion: MotionCfg = Field(default_factory=MotionCfg)
    default_video: str | None = None


class Line(BaseModel):
    id: str
    name: str
    machines: list[Machine] = Field(default_factory=list)
    cameras: list[Camera] = Field(default_factory=list)


class Plant(BaseModel):
    lines: list[Line] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        for line in self.lines:
            for m in line.machines:
                m.line = line.id
            for c in line.cameras:
                c.line = line.id

    @classmethod
    def load(cls, path: Path) -> "Plant":
        return cls(**yaml.safe_load(path.read_text(encoding="utf-8")))

    @property
    def machines(self) -> dict[str, Machine]:
        return {m.id: m for line in self.lines for m in line.machines}

    @property
    def cameras(self) -> dict[str, Camera]:
        return {c.id: c for line in self.lines for c in line.cameras}

    def line(self, line_id: str) -> Line | None:
        return next((l for l in self.lines if l.id == line_id), None)

    def cameras_for_machine(self, machine_id: str) -> list[Camera]:
        return [c for c in self.cameras.values() if c.machine_id == machine_id]

    def line_of(self, entity: str | None) -> str | None:
        if not entity:
            return None
        if entity in self.machines:
            return self.machines[entity].line
        if entity in self.cameras:
            return self.cameras[entity].line
        return None
