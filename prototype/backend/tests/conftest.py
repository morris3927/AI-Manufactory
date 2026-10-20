"""共用 fixture：暫存資料庫與知識庫、手動時鐘、假 Jev／LLM。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeJev, FakeLlm  # noqa: E402

from sentinel.clock import ManualClock  # noqa: E402
from sentinel.config import BACKEND_DIR, load_settings  # noqa: E402
from sentinel.dispatch.notifier import ConsoleNotifier  # noqa: E402
from sentinel.runtime import Runtime  # noqa: E402

# 測試專用的最小知識庫（不是正式知識內容；正式內容在 prototype/knowledge/）
FIXTURE_FM = {"stamping": {
    "feed_roller_wear": {"label": "送料滾輪磨損／打滑", "criteria": "送料長度誤差在數分鐘內逐步累積", "sop": "SOP-STP-012",
                         "machine_type": ["servo_roll_feeder"]},
    "material_thickness_dev": {"label": "材料厚度偏差", "criteria": "換料捲後短時間內發生", "sop": "SOP-STP-007"},
    "servo_feeder_drive_alarm": {"label": "伺服送料機驅動器警報", "criteria": "送料瞬間停止（非逐步）"},
}}

FIXTURE_SOP = {
    "sop_id": "SOP-STP-012", "version": "0.1", "title": "送料滾輪磨損之檢查與更換（測試）", "line": "stamping",
    "machine_type": ["servo_roll_feeder"], "failure_mode_ids": ["feed_roller_wear"], "data_label": "模擬資料",
    "synthetic": True, "review_status": "approved",
    "safety": {"loto_required": True, "loto_checklist_id": "LOTO-FEED-01"},
    "symptoms": ["送料偏短"],
    "steps": [
        {"no": 1, "phase": "prepare", "action": "通知操作員與班長停線"},
        {"no": 2, "phase": "isolate", "action": "依 LOTO-FEED-01 停機、斷電、洩壓、上鎖掛牌"},
        {"no": 3, "phase": "inspect", "action": "目視滾輪表面亮帶、油污；量測直徑"},
        {"no": 4, "phase": "repair", "action": "更換上下滾輪"},
    ],
    "verify": ["無料空轉量測送料誤差", "試沖 20 模"],
    "restore": ["依 LOTO-FEED-01 解除程序，由原上鎖人取下鎖具"],
}

FIXTURE_LOTO = {
    "loto_id": "LOTO-FEED-01", "machine_type": "servo_roll_feeder", "data_label": "模擬資料",
    "permit": {"required": False},
    "steps": [{"no": 1, "action": "通知受影響人員", "check": "簽名"},
              {"no": 2, "action": "停止送料、停機", "check": "目視"},
              {"no": 3, "action": "切斷 QS2 並掛個人鎖", "check": "鎖號"}],
    "release": ["各人取下自己的鎖"],
}

FIXTURE_CASE = {
    "case_id": "CASE-STP-0001", "data_label": "模擬資料", "synthetic": True, "line": "stamping",
    "machine_type": "servo_roll_feeder", "machine_id": "STP-L1-FD02",
    "trigger": {"source": "fused", "event_type": "feed_jam_suspected", "rule_severity": "S2"},
    "context": {"days_since_pm": 41, "recent_changes": ["換料捲 09:50"]},
    "log_signals": [{"t": "10:22:00", "signal": "feed_length_error_mm", "value": 0.08}],
    "first_hypothesis": {"failure_mode_id": "material_thickness_dev", "by": "jev", "p": 0.46},
    "final_root_cause": "feed_roller_wear", "hypothesis_correct": False,
    "why_first_guess_wrong": "誤差逐步累積", "actions": [{"step": "更換滾輪", "result": "OK"}],
    "review_status": "approved",
}


def write_knowledge(root: Path) -> Path:
    (root / "failure_modes").mkdir(parents=True)
    (root / "failure_modes" / "stamping.yaml").write_text(yaml.safe_dump(FIXTURE_FM, allow_unicode=True), "utf-8")
    (root / "sop" / "stamping").mkdir(parents=True)
    (root / "sop" / "stamping" / "SOP-STP-012.yaml").write_text(yaml.safe_dump(FIXTURE_SOP, allow_unicode=True), "utf-8")
    draft = dict(FIXTURE_SOP, sop_id="SOP-STP-099", review_status="draft", failure_mode_ids=["material_thickness_dev"])
    (root / "sop" / "stamping" / "SOP-STP-099.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True), "utf-8")
    (root / "loto").mkdir()
    (root / "loto" / "LOTO-FEED-01.yaml").write_text(yaml.safe_dump(FIXTURE_LOTO, allow_unicode=True), "utf-8")
    (root / "cases" / "stamping").mkdir(parents=True)
    (root / "cases" / "stamping" / "CASE-STP-0001.json").write_text(json.dumps(FIXTURE_CASE, ensure_ascii=False), "utf-8")
    return root


@pytest.fixture
def knowledge(tmp_path: Path) -> Path:
    return write_knowledge(tmp_path / "knowledge")


@pytest.fixture
def settings(tmp_path: Path, knowledge: Path):
    return load_settings(BACKEND_DIR / "config.yaml",
                         paths={"db": str(tmp_path / "sentinel.db"), "chroma": str(tmp_path / "chroma"),
                                "knowledge": str(knowledge), "drafts": str(tmp_path / "drafts")},
                         kb={"embedding": "hash"}, jev={"warmup": False}, app={"ambient": False})


@pytest.fixture
def clock(settings) -> ManualClock:
    return ManualClock(settings.app.sim_start)


@pytest.fixture
def fake_jev(clock) -> FakeJev:
    return FakeJev(clock.monotonic)


@pytest.fixture
def fake_llm() -> FakeLlm:
    return FakeLlm()


@pytest.fixture
async def rt(settings, clock, fake_jev, fake_llm):
    runtime = Runtime(settings, clock=clock, notifier=ConsoleNotifier(),
                      jev_transport=httpx.MockTransport(fake_jev), llm_transport=httpx.MockTransport(fake_llm),
                      jev_key="test-key", llm_key="test-key")
    from sentinel.kb.ingest import ingest

    if runtime.kb.store is not None:
        ingest(settings.path("knowledge"), runtime.kb.store)
    await runtime.start(realtime=False, warmup=False)
    yield runtime
    await runtime.stop()
