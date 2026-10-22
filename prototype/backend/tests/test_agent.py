"""維修 Agent 的程式強制限制：LOTO 先行、復電過濾、J4 安全把關、不能改工單狀態；結案回報（J5）。"""
from __future__ import annotations

import json

import pytest
from sqlmodel import select

from sentinel.agent import guards
from sentinel.db.models import Notification, Workorder
from sentinel.dispatch.closeout import submit_report


async def open_feed_jam(rt) -> Workorder:
    await rt.engine.run_stepped(rt.scenarios["feed_jam"], duration_s=16)
    return rt.db.first(select(Workorder))


async def loto_done(rt, wo_id: str) -> None:
    """接單 → 申請作業許可 → 班長核准 → 勾完 LOTO（自動進入 IN_REPAIR）。"""
    await rt.workorders.ack(wo_id, "P-STP-TC1")
    await rt.workorders.permit(wo_id, "P-STP-TC1", "request")
    await rt.workorders.permit(wo_id, "P-STP-LD1", "approve")
    loto = rt.agent.ctx.loto_for(wo_id)
    for s in loto.steps:
        await rt.workorders.check_loto(wo_id, s.no, "P-STP-TC1", loto_id=loto.loto_id, steps=[x.no for x in loto.steps])


# ── 復電過濾 ────────────────────────────────────────────────────
SOP_TEST_STEP = ("需要通電量測或移動滑塊時，依 LOTO-FEED-01 測試／定位程序：需要通電量測送料長度時：清除人員、"
                 "由原上鎖人取下自己的鎖、現場主管在場；只用寸動或送料機單獨試送；量測完成後重新上鎖並驗證零能量")
SOP_RESTART_STEP = ("試沖與連續生產屬於恢復生產，不是測試／定位：先清點工具、撤出安全塊、恢復護圍；"
                    "由原上鎖人取下自己的鎖後，才由操作員以正常操作模式試沖。")


@pytest.mark.parametrize("text", ["處理完成後即可復電試運轉。", "確認後可以解除上鎖。", "2. 取下鎖具並送電\n",
                                  "請合上主電源開關。", "Then re-energize the press.", SOP_RESTART_STEP])
def test_restore_filter_drops_and_reminds(text):
    r = guards.filter_restore(text, "LOTO-FEED-01")
    assert r.removed and not r.rewritten
    assert r.text.strip().endswith(guards.CLOSING_REMINDER)
    body = r.text.replace(guards.CLOSING_REMINDER, "")
    assert not any(p.search(body) for p in guards.RESTORE_PATTERNS)
    assert guards.CLOSING_REMINDER not in guards.extract_steps(r.text)       # 提醒不是步驟


@pytest.mark.parametrize("text", ["禁止復電，等待主管確認。", "不得解除上鎖。", "目視滾輪表面是否有亮帶。"])
def test_restore_filter_keeps_safe_text(text):
    r = guards.filter_restore(text)
    assert r.text == text and r.removed == [] and r.rewritten == []


def test_restore_filter_rewrites_test_positioning_step():
    """live 測試的 bug：SOP 的測試／定位步驟不能變成一句「請由原上鎖人依 LOTO 解除程序執行」當第 1 步。"""
    text = f"先驗證滾輪磨損。\n1. {SOP_TEST_STEP}\n2. 以卡尺量測滾輪直徑\n3. 完成後即可復電試運轉"
    r = guards.filter_restore(text, "LOTO-FEED-01")
    lines = r.text.splitlines()
    assert lines[1] == "1. " + guards.ENERGIZED_TEMPLATE.format(loto_id="LOTO-FEED-01")
    assert lines[2] == "2. 以卡尺量測滾輪直徑"
    assert lines[-1] == guards.CLOSING_REMINDER and len(lines) == 4           # 第 3 步刪掉，結尾提醒不是步驟
    assert guards.extract_steps(r.text) == [guards.ENERGIZED_TEMPLATE.format(loto_id="LOTO-FEED-01"), "以卡尺量測滾輪直徑"]
    assert r.rewritten and r.removed


def test_restore_filter_renumbers():
    r = guards.filter_restore("1. 檢查滾輪\n2. 完成後恢復供電\n3. 填寫回報")
    assert r.text == f"1. 檢查滾輪\n2. 填寫回報\n{guards.CLOSING_REMINDER}"


def test_extract_steps():
    assert guards.extract_steps("說明\n1. 甲\n2）乙\n- 丙") == ["甲", "乙", "丙"]


# ── LOTO 先行 ───────────────────────────────────────────────────
def test_tool_guard_table():
    for st in ("OPEN", "ASSIGNED", "ESCALATED", "ACKED", "PERMIT_REQUESTED", "PERMIT_APPROVED"):
        with pytest.raises(guards.ToolDenied):
            guards.check_tool("get_sop", {"failure_mode_id": "x", "phase": "inspect"}, st)
        with pytest.raises(guards.ToolDenied):
            guards.check_tool("get_machine_signals", {"signal": "feed_rate"}, st)
    guards.check_tool("get_loto", {}, "ACKED")
    guards.check_tool("get_sop", {"failure_mode_id": "x", "phase": "repair"}, "IN_REPAIR")
    for st in ("PERMIT_REQUESTED", "PERMIT_APPROVED", "LOTO_DONE"):   # 檢查／維修段落只在 IN_REPAIR 之後
        with pytest.raises(guards.ToolDenied):
            guards.check_tool("get_sop", {"failure_mode_id": "x", "phase": "repair"}, st)
    assert "set_status" not in guards.ALL_TOOLS and not any("status" in t for t in guards.ALL_TOOLS)


async def test_agent_loto_first(rt, fake_llm):
    wo = await open_feed_jam(rt)
    await rt.workorders.ack(wo.id, "P-STP-TC1")
    llm_before = len(fake_llm.calls)
    reply = await rt.agent.handle(wo.id, "卡料了，要怎麼處理？", "P-STP-TC1")
    assert reply.phase == "loto"
    assert "LOTO-FEED-01" in reply.content and "☐ 1." in reply.content
    assert "請先申請作業許可" in reply.content and "預覽" in reply.content          # 許可核准前：只有許可／LOTO 預覽
    assert "目視滾輪" not in reply.content and "更換上下滾輪" not in reply.content   # 沒有 inspect／repair
    assert "依 LOTO-FEED-01 停機" in reply.content                                  # isolate 段落可以給
    assert len(fake_llm.calls) == llm_before                                         # 不呼叫 LLM
    with pytest.raises(guards.ToolDenied):
        from sentinel.agent.tools import ToolBox
        await ToolBox(rt.agent.ctx, wo.id).call("get_sop", {"failure_mode_id": "feed_roller_wear", "phase": "inspect"})
    assert rt.workorders.get(wo.id).status == "ACKED"                                # Agent 沒有改狀態


async def test_agent_after_loto_gates_and_filters(rt, fake_llm, fake_jev):
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    reply = await rt.agent.handle(wo.id, "LOTO 完成了，下一步？", "P-STP-TC1")
    assert reply.phase == "repair" and reply.llm_used and reply.hypothesis == "feed_roller_wear"
    # 復電指示被刪掉，結尾加提醒；測試／定位步驟改寫成固定句型並要求主管
    assert "復電試運轉" not in reply.content and reply.content.endswith(guards.CLOSING_REMINDER)
    assert guards.ENERGIZED_MARK in reply.content and "energized_step_rewritten" in reply.safety_flags
    energized = [s for s in reply.steps if s.source == "energized_test"]
    assert energized and energized[0].requires_supervisor
    benign = [s for s in reply.steps if "目視" in s.text]
    assert benign and not benign[0].flagged            # LOTO 已完成：needs_loto 不會單獨觸發主管確認
    assert "restore_power_filtered" in reply.safety_flags
    # J4：「伸手取出」被標紅 → 自動要求主管確認
    flagged = [s.text for s in reply.steps if s.flagged]
    assert any("伸手" in t for t in flagged) and reply.supervisor_requested
    assert rt.db.first(select(Notification).where(Notification.kind == "supervisor"))
    assert rt.workorders.get(wo.id).status == "IN_REPAIR"
    # 動態排查：回報無效 → 換下一個根因
    reply2 = await rt.agent.handle(wo.id, "檢查過滾輪了，沒改善", "P-STP-TC1")
    assert reply2.hypothesis == "material_thickness_dev"


async def test_agent_llm_down_shows_sop(rt, fake_llm):
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    fake_llm.fail = True
    reply = await rt.agent.handle(wo.id, "下一步？", "P-STP-TC1")
    assert not reply.llm_used and "SOP 原文" in reply.content and "目視滾輪" in reply.content


async def test_closeout_flow(rt):
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    items = rt.kb.checklist_for("feed_roller_wear")
    assert items == ["無料空轉量測送料誤差", "試沖 20 模"]
    for it in items:
        await rt.workorders.check_item(wo.id, it, "P-STP-TC1", items)
    assert rt.workorders.get(wo.id).status == "PENDING_VERIFY"
    bad = await submit_report(rt, wo.id, "P-STP-TC1", "修好了")
    assert bad["ok"] is False and rt.workorders.get(wo.id).status == "PENDING_VERIFY"
    assert bad["check"]["missing"] and bad["check"]["kb_status"] == "rejected"
    good = await submit_report(rt, wo.id, "P-STP-TC1", "下滾輪亮帶加油污，清潔無效後更換上下滾輪，送料誤差恢復 0.02 mm")
    assert good["ok"] and good["status"] == "CLOSED" and good["case_draft"] == "CASE-STP-WB20261030-0001"
    draft = rt.settings.path("drafts") / "stamping" / f"{good['case_draft']}.json"
    assert '"review_status": "draft"' in draft.read_text(encoding="utf-8")
    assert rt.workorders.incident(rt.workorders.get(wo.id)).status == "closed"


async def test_audit_append_only(rt):
    import sqlalchemy

    rt.audit.record("x")
    with pytest.raises(sqlalchemy.exc.DatabaseError):
        with rt.db.engine.begin() as c:
            c.execute(sqlalchemy.text("UPDATE audit_log SET action='y'"))
    with pytest.raises(sqlalchemy.exc.DatabaseError):
        with rt.db.engine.begin() as c:
            c.execute(sqlalchemy.text("DELETE FROM audit_log"))



# ── 越用越準：結案回寫 → 主管核准 → 立即入庫 → 下一次 Jev 判斷帶到這筆案例 ──
async def close_feed_jam(rt, wo_id: str, root_cause: str) -> dict:
    await loto_done(rt, wo_id)
    items = rt.kb.checklist_for((rt.dispatch.diagnosis_for(rt.workorders.get(wo_id).incident_id).root_cause_top3
                                 or [{}])[0].get("id"))
    for it in items:
        await rt.workorders.check_item(wo_id, it, "P-STP-TC1", items)
    return await submit_report(rt, wo_id, "P-STP-TC1", "料捲換新批後板厚 1.08 mm 超出公差，換回前一捲後送料恢復正常",
                               root_cause)


def judge_similar_cases(fake_jev) -> list[str]:
    calls = [c for c in fake_jev.calls if "category" in c["questions"]]
    return calls[-1]["state"]["similar_cases"]


async def test_writeback_approval_feeds_next_judgement(rt, fake_jev):
    sc = rt.scenarios["feed_jam"]
    await rt.engine.run_stepped(sc)
    wo1 = rt.db.first(select(Workorder))
    before = judge_similar_cases(fake_jev)
    res = await close_feed_jam(rt, wo1.id, "material_thickness_dev")
    case_id = res["case_draft"]
    assert not any(case_id in c for c in before)
    assert [c["case_id"] for c in rt.writeback.list("draft")] == [case_id]

    # 草稿還沒核准：重跑劇本，Jev 看不到這筆
    await rt.engine.run_stepped(sc)
    assert not any(case_id in c for c in judge_similar_cases(fake_jev))
    wo2 = rt.db.all(select(Workorder))[-1]
    await rt.workorders.transition(wo2.id, "CANCELLED", actor="P-STP-LD1", reason="測試：同一事件重跑")
    n_calls = len(fake_jev.calls)

    doc, n = rt.writeback.approve(case_id, reviewer="P-STP-SV1", at=rt.clock.now())
    assert n == 2 and doc["review_status"] == "approved" and doc["reviewed_by"] == ["P-STP-SV1"]
    assert rt.writeback.list("draft") == []

    # 核准後重跑：同機台、同事件的真實結案紀錄排在 similar_cases 第一位
    await rt.engine.run_stepped(sc)
    assert len(rt.db.all(select(Workorder))) == 3 and len(fake_jev.calls) > n_calls
    after = judge_similar_cases(fake_jev)
    assert case_id in after[0] and "material_thickness_dev" in after[0]



async def test_permit_before_loto_order(rt):
    from sentinel.dispatch.workorder import PermissionDenied, TransitionError

    wo = await open_feed_jam(rt)
    await rt.workorders.ack(wo.id, "P-STP-TC1")
    loto = rt.agent.ctx.loto_for(wo.id)
    steps = [s.no for s in loto.steps]
    with pytest.raises(TransitionError, match="作業許可尚未核准"):
        await rt.workorders.check_loto(wo.id, 1, "P-STP-TC1", loto_id=loto.loto_id, steps=steps)
    with pytest.raises(TransitionError, match="沒有待核准"):
        await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")
    await rt.workorders.permit(wo.id, "P-STP-TC1", "request")
    with pytest.raises(TransitionError, match="作業許可尚未核准"):
        await rt.workorders.check_loto(wo.id, 1, "P-STP-TC1", loto_id=loto.loto_id, steps=steps)
    with pytest.raises(PermissionDenied):
        await rt.workorders.permit(wo.id, "P-STP-TC1", "approve")            # 技術員不能核准
    assert (await rt.workorders.permit(wo.id, "P-STP-LD1", "reject")).status == "ACKED"
    await rt.workorders.permit(wo.id, "P-STP-TC1", "request")
    assert (await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")).status == "PERMIT_APPROVED"
    r = await rt.agent.handle(wo.id, "可以開始了嗎", "P-STP-TC1")
    assert r.phase == "loto" and "預覽" not in r.content                      # 核准後：LOTO 檢查表本身
    for n in steps:
        w, done = await rt.workorders.check_loto(wo.id, n, "P-STP-TC1", loto_id=loto.loto_id, steps=steps)
    assert done and w.status == "IN_REPAIR"
    trs = [t.to_status for t in rt.workorders.transitions(wo.id)]
    assert trs == ["OPEN", "ASSIGNED", "ACKED", "PERMIT_REQUESTED", "ACKED", "PERMIT_REQUESTED", "PERMIT_APPROVED",
                   "LOTO_DONE", "IN_REPAIR"]


# ── 串流與延遲最佳化（首字／安全旗標分開送；每步寫完就送 J4）──────────────
@pytest.mark.parametrize("text", [
    "先看滾輪。\n1. 目視滾輪\n2. 處理完成後即可復電試運轉。\n3. 量測直徑\n",
    "1. 需要通電量測送料長度時，依測試／定位程序由原上鎖人取下自己的鎖\n2. 伸手取出板材",
    "說明一行沒有換行結尾。可以解除上鎖。",
])
def test_stream_filter_matches_full_filter(text):
    sf = guards.StreamFilter("LOTO-FEED-01")
    shown: list[str] = []
    for i in range(0, len(text), 3):                       # 3 個字一段，隨機切在句中
        shown += sf.feed(text[i:i + 3])
    shown += sf.finish()
    full = guards.filter_restore(text, "LOTO-FEED-01")
    assert "\n".join(shown) == full.text == sf.text
    assert not any(guards.classify(line) == "restore" for line in shown if line != guards.CLOSING_REMINDER)


async def test_agent_streams_filtered_lines_then_flags(rt, fake_llm, fake_jev):
    from sentinel.schemas.events import Topic

    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    q = rt.bus.subscribe(Topic.AI, "t-stream", maxsize=1000)
    reply = await rt.agent.handle(wo.id, "LOTO 完成了，下一步？", "P-STP-TC1")
    evts = []
    while not q.empty():
        e = q.get_nowait()
        if e.payload.get("workorder_id") == wo.id:
            evts.append(e)
    types = [e.type for e in evts]
    deltas = [e.payload for e in evts if e.type == "agent_delta"]
    assert len(deltas) >= 3                                               # 逐行送出
    assert types.index("agent_delta") < types.index("agent_reply") < len(types) - 1
    assert all("復電試運轉" not in d["text"] for d in deltas)              # 未過濾的字不會出現在畫面
    assert deltas[-1]["text"].endswith(guards.CLOSING_REMINDER)
    mid = {d["message_id"] for d in deltas}
    assert mid == {reply.message_id}                                      # 串流與最終訊息同一個 id
    rep = next(e.payload for e in evts if e.type == "agent_reply")
    assert rep["gate_pending"] and rep["message"]["gate"]["status"] == "pending"
    gates = [e.payload for e in evts if e.type == "agent_safety_gate"]
    assert gates[-1]["done"] and gates[-1]["gate"]["status"] == "done" and types[-1] == "agent_safety_gate"
    assert any("伸手" in f["step_text"] for f in gates[-1]["flags"])
    # 存檔：最終旗標、把關狀態 done，標記不當工具顯示
    from sentinel.agent.repair_agent import message_dict
    from sentinel.db.models import AgentMessage
    row = rt.db.get(AgentMessage, int(reply.message_id))
    d = message_dict(row)
    assert d["gate"]["status"] == "done" and d["safety_flags"] == gates[-1]["flags"]
    assert all(t["name"] != "safety_gate" for t in d["tool_calls"])
    # 只呼叫一次 LLM、stream=true、think=false、沒有給工具（資料已預先放進提示詞）
    body = fake_llm.calls[-1]
    assert body["stream"] is True and body["think"] is False and "tools" not in body
    ctx = json.loads(body["messages"][1]["content"].split("\n", 1)[1])
    assert {"workorder", "sop", "loto", "signals", "similar_cases"} <= ctx.keys() and ctx["loto"]["all_checked"]
    assert reply.timings["llm_calls"] == 1 and reply.timings["tool_rounds"] == 0
    assert reply.timings["first_text_ms"] <= reply.timings["total_ms"]


async def test_gate_starts_while_llm_still_streaming(rt, fake_llm, fake_jev):
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    log: list[str] = []
    fake_llm.log = fake_jev.log = log
    fake_llm.delay_s = 0.01
    reply = await rt.agent.handle(wo.id, "下一步？", "P-STP-TC1")
    first_jev = next(i for i, x in enumerate(log) if x.startswith("jev:"))
    assert first_jev < log.index("llm:done")                             # 第 1 步一寫完就送 J4，不等全文
    jev_steps = [x for x in log if x.startswith("jev:")]
    gated = [s for s in reply.steps if s.source == "jev"]
    assert len(jev_steps) == len(gated) == reply.timings["gate_requests"]   # 每步一次，沒有重複送
    assert any(s.flagged and "伸手" in s.text for s in reply.steps)


async def test_tool_rounds_capped(rt, fake_llm):
    from sentinel.agent import repair_agent

    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    rt.agent.ctx.agent_tool_rounds = 5
    fake_llm.tool_first = True
    n0 = len(fake_llm.calls)
    reply = await rt.agent.handle(wo.id, "下一步？", "P-STP-TC1")
    calls = fake_llm.calls[n0:]
    assert reply.timings["tool_rounds"] == 1 and len(calls) == 2           # 工具一輪後就給最終回覆
    assert len(calls) <= repair_agent.MAX_TOOL_ROUNDS + 1
    assert any(m["role"] == "tool" for m in calls[-1]["messages"])
    assert "目視" in reply.content and guards.CLOSING_REMINDER in reply.content


async def test_gate_steps_parallel_in_order(rt, fake_jev):
    from sentinel.jev import gate

    texts = ["目視滾輪", "伸手取出卡料", "填寫紀錄"]
    res = await gate.steps(rt.jev, texts, "servo_roll_feeder", thresholds=rt.settings.thresholds.gate, loto_done=True)
    assert [r.step for r in res] == texts
    assert [r.flagged for r in res] == [False, True, False]                 # 門檻來自 config，LOTO 後 needs_loto 不單獨觸發
    fake_jev.fail = True
    res = await gate.steps(rt.jev, texts, None)
    assert all(isinstance(r, Exception) for r in res)


async def test_llm_stream_failure_falls_back_to_sop(rt, fake_llm):
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    fake_llm.fail = True
    reply = await rt.agent.handle(wo.id, "下一步？", "P-STP-TC1")
    assert not reply.llm_used and "SOP 原文" in reply.content and reply.steps    # SOP 原文一樣過 J4


def test_ws_maps_stream_and_gate_events():
    from datetime import datetime

    from sentinel.api.ws import to_messages
    from sentinel.clock import TW
    from sentinel.schemas.events import Event, Topic

    ts = datetime(2026, 10, 30, 9, tzinfo=TW)
    out = to_messages(Event(ts=ts, topic=Topic.AI, source="agent", type="agent_delta",
                            payload={"workorder_id": "WO-1", "message_id": "9", "text": "a\n1. b", "chunk": "1. b"}))
    assert [(m["channel"], m["type"], m["data"]["text"]) for m in out] == [("agent:WO-1", "delta", "a\n1. b")]
    out = to_messages(Event(ts=ts, topic=Topic.AI, source="agent", type="agent_safety_gate",
                            payload={"workorder_id": "WO-1", "message_id": "9", "flags": [], "done": True,
                                     "gate": {"status": "done", "checked": ["b"]}}))
    assert out[0]["type"] == "safety_gate" and out[0]["data"]["done"] and out[0]["data"]["gate"]["status"] == "done"
