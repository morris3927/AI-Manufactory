"""真實 API 測試（需要根目錄 .env 的金鑰；預設略過，用 pytest -m live 執行）。每次花費約 US$0.0001。"""
from __future__ import annotations

import pytest

from sentinel.config import load_settings
from sentinel.jev import vision as jev_vision
from sentinel.jev.client import JevClient
from sentinel.jev.judge import score_to_severity
from sentinel.jev.questions import SEVERITY_CRITERIA
from sentinel.llm.client import LlmClient
from sentinel.sim.camera import synthetic_frame

pytestmark = pytest.mark.live
S = load_settings()


def jev() -> JevClient:
    key = S.secret(S.secrets.openrouter_api_key)
    if not key:
        pytest.skip("沒有 OPENROUTER_API_KEY")
    return JevClient(api_key=key, url=S.jev.url, model=S.jev.model, timeout_s=10)


async def test_live_jev_text():
    c = jev()
    try:
        r = await c.ask({"event": "沖床送料速率 10 秒內由 42 spm 降到 0，影像判定送料口有板材，單一機台，無人員受傷"},
                        {"is_jam": {"type": "noul", "instructions": "這是否為送料卡料？"},
                         "severity": {"type": "score", "instructions": "評定嚴重度", "criteria": SEVERITY_CRITERIA}},
                        purpose="live_test")
    finally:
        await c.aclose()
    assert r.noul("is_jam") > 0.5
    assert score_to_severity(r.score("severity").score) in ("S2", "S3")
    assert r.model.startswith("typesafe/jev-1.14")


async def test_live_jev_vision():
    c = jev()
    try:
        frame = synthetic_frame("feed_jam", 12.0)
        j = await jev_vision.feed(c, frame, [420, 310, 860, 620], "伺服送料機")
    finally:
        await c.aclose()
    assert 0 <= j.p_jam <= 1 and j.latency_ms is not None


async def test_live_llm():
    key = S.secret(S.secrets.ollama_api_key)
    if not key:
        pytest.skip("沒有 OLLAMA_API_KEY")
    c = LlmClient(api_key=key, url=S.llm.url, model=S.llm.model, timeout_s=60)
    try:
        r = await c.chat([{"role": "user", "content": "用繁體中文一句話說明什麼是上鎖掛牌（LOTO），20 字以內。"}],
                         purpose="live_test")
    finally:
        await c.aclose()
    assert r.content.strip()
