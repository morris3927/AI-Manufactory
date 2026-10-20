"""設定：根目錄 .env（金鑰）＋ backend/config.yaml（模型、門檻、路徑）。"""
from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROTOTYPE_DIR = BACKEND_DIR.parent
REPO_ROOT = PROTOTYPE_DIR.parent


class AppCfg(BaseModel):
    timezone: str = "Asia/Taipei"
    sim_start: datetime = datetime.fromisoformat("2026-10-30T09:00:00+08:00")
    ambient: bool = True


class PathsCfg(BaseModel):
    plant: str = "data/simulated/plant.yaml"
    roster: str = "data/simulated/roster.csv"
    access: str = "data/simulated/access.csv"
    scenarios: str = "data/simulated/scenarios"
    knowledge: str = "knowledge"
    drafts: str = "knowledge/cases/_drafts"
    videos_root: str = "."
    models: str = "models"
    db: str = "backend/sentinel.db"
    chroma: str = "backend/chroma"
    keyframes: str = "backend/keyframes"


class JevCfg(BaseModel):
    url: str = "https://openrouter.ai/api/alpha/decisions"
    model: str = "typesafe/jev-1.14"
    timeout_s: float = 3.0
    enabled: bool = True
    warmup: bool = True


class LlmCfg(BaseModel):
    url: str = "https://ollama.com/api/chat"
    model: str = "deepseek-v4.1-flash"
    timeout_s: float = 30.0
    enabled: bool = True


class KbCfg(BaseModel):
    embedding: str = "auto"
    embedding_model: str = "BAAI/bge-m3"
    allow_draft: bool = False


class VisionCfg(BaseModel):
    fps: float = 15
    ppe_fps: float = 5
    keyframe_interval_s: float = 5
    jpeg_quality: int = 85
    jev_max_side: int = 768
    yolo_weights: str = "models/ppe.pt"
    yolo_device: str = "mps"
    yolo_classes: dict[str, list[str]] = Field(default_factory=dict)
    ppe_jev_confirm: bool = False


class Thresholds(BaseModel):
    p_jam: float = 0.7
    p_jam_count: int = 2
    p_jam_window_s: float = 15
    feed_drop_window_s: float = 30
    jev_stale_s: float = 30
    ppe_overlap: float = 0.3
    ppe_conf: float = 0.5
    ppe_dedupe_s: float = 3
    ppe_window_s: float = 5
    log_mismatch_s: float = 20
    p_false_alarm: float = 0.9
    root_cause_conf: float = 0.6
    safety_gate_p: float = 0.3
    writeback_complete: float = 0.7
    sop_top1: float = 0.6
    sop_low: float = 0.3
    incident_update_s: float = 5


class EscalationCfg(BaseModel):
    ack_timeout_s: dict[str, float] = Field(default_factory=lambda: {"S1": 600, "S2": 300, "S3": 120})
    demo_ack_timeout_s: float | None = None

    def timeout_for(self, severity: str) -> float:
        if self.demo_ack_timeout_s:
            return float(self.demo_ack_timeout_s)
        return float(self.ack_timeout_s.get(severity, 300))


class MesCfg(BaseModel):
    tick_s: float = 1.0
    seed: int = 42


class DiscordCfg(BaseModel):
    channels: dict[str, str] = Field(default_factory=dict)


class Secrets(BaseSettings):
    """只讀取金鑰；值一律用 SecretStr，不會被印出。"""

    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    openrouter_api_key: SecretStr | None = None
    ollama_api_key: SecretStr | None = None
    discord_bot_token: SecretStr | None = None


class Settings(BaseModel):
    app: AppCfg = Field(default_factory=AppCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)
    jev: JevCfg = Field(default_factory=JevCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    kb: KbCfg = Field(default_factory=KbCfg)
    vision: VisionCfg = Field(default_factory=VisionCfg)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    escalation: EscalationCfg = Field(default_factory=EscalationCfg)
    mes: MesCfg = Field(default_factory=MesCfg)
    discord: DiscordCfg = Field(default_factory=DiscordCfg)
    secrets: Secrets = Field(default_factory=Secrets, exclude=True)
    base_dir: Path = PROTOTYPE_DIR

    def path(self, name: str) -> Path:
        p = Path(getattr(self.paths, name))
        return p if p.is_absolute() else (self.base_dir / p)

    @staticmethod
    def secret(value: SecretStr | None) -> str | None:
        v = value.get_secret_value().strip() if value else ""
        return v or None


def load_settings(config_path: str | Path | None = None, **overrides: Any) -> Settings:
    path = Path(config_path or os.environ.get("SENTINEL_CONFIG", BACKEND_DIR / "config.yaml"))
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    st = Settings(**data)
    if os.environ.get("SENTINEL_KB_ALLOW_DRAFT", "").lower() in {"1", "true", "yes"}:
        st.kb.allow_draft = True          # 只限 Demo：放行 review_status=draft 的 SOP（make dev-demo）
    return st


@lru_cache
def get_settings() -> Settings:
    return load_settings()
