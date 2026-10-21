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


async def test_live_writeback_before_after(settings, clock, fake_llm):
    """越用越準（真實 Jev）：結案根因核准入庫後重跑同一劇本，記錄 J3 機率的變化（不保證方向，只記錄）。"""
    import httpx
    from sqlmodel import select
    from test_agent import close_feed_jam

    from sentinel.db.models import Workorder
    from sentinel.dispatch.notifier import ConsoleNotifier
    from sentinel.kb.ingest import ingest
    from sentinel.runtime import Runtime

    key = S.secret(S.secrets.openrouter_api_key)
    if not key:
        pytest.skip("沒有 OPENROUTER_API_KEY")
    rt = Runtime(settings, clock=clock, notifier=ConsoleNotifier(), llm_transport=httpx.MockTransport(fake_llm),
                 jev_key=key, llm_key="k")
    ingest(settings.path("knowledge"), rt.kb.store)
    await rt.start(realtime=False, warmup=False)
    target = "material_thickness_dev"
    try:
        sc = rt.scenarios["feed_jam"]
        await rt.engine.run_stepped(sc)
        wo1 = rt.db.first(select(Workorder))
        before = rt.dispatch.diagnosis_for(wo1.incident_id).root_cause_top3
        res = await close_feed_jam(rt, wo1.id, target)
        rt.writeback.approve(res["case_draft"], reviewer="P-STP-SV1", at=rt.clock.now())
        await rt.engine.run_stepped(sc)
        wo2 = rt.db.all(select(Workorder))[-1]
        after = rt.dispatch.diagnosis_for(wo2.incident_id).root_cause_top3
    finally:
        await rt.stop()
    p = lambda top3: next((r["p"] for r in top3 if r["id"] == target), 0.0)  # noqa: E731
    print(f"\n[越用越準] 核准前 top1={before[0]['id']} {before[0]['p']:.2f}；{target}={p(before):.2f}"
          f"\n[越用越準] 核准後 top1={after[0]['id']} {after[0]['p']:.2f}；{target}={p(after):.2f}")
    assert before and after
