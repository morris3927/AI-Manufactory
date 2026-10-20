"""維修 Agent 的程式強制限制：LOTO 先行、復電過濾、J4 安全把關、不能改工單狀態；結案回報（J5）。"""
from __future__ import annotations

import pytest
from sqlmodel import select

from sentinel.agent import guards
from sentinel.db.models import Notification, Workorder
from sentinel.dispatch.closeout import submit_report


async def open_feed_jam(rt) -> Workorder:
    await rt.engine.run_stepped(rt.scenarios["feed_jam"], duration_s=16)
    return rt.db.first(select(Workorder))


async def loto_done(rt, wo_id: str) -> None:
    await rt.workorders.ack(wo_id, "P-STP-TC1")
    loto = rt.agent.ctx.loto_for(wo_id)
    for s in loto.steps:
        await rt.workorders.check_loto(wo_id, s.no, "P-STP-TC1", loto_id=loto.loto_id, steps=[x.no for x in loto.steps])


# ── 復電過濾 ────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["處理完成後即可復電試運轉。", "確認後可以解除上鎖。", "2. 取下鎖具並送電\n",
                                  "請合上主電源開關。", "Then re-energize the press."])
def test_restore_filter_replaces(text):
    out, removed = guards.filter_restore(text)
    assert removed and guards.RESTORE_REPLACEMENT in out
    assert not any(p.search(out) for p in guards.RESTORE_PATTERNS)


@pytest.mark.parametrize("text", ["禁止復電，等待主管確認。", "不得解除上鎖。", "目視滾輪表面是否有亮帶。"])
def test_restore_filter_keeps_safe_text(text):
    out, removed = guards.filter_restore(text)
    assert out == text and removed == []


def test_restore_filter_keeps_numbering():
    out, _ = guards.filter_restore("1. 檢查滾輪\n2. 完成後恢復供電\n")
    assert out == f"1. 檢查滾輪\n2. {guards.RESTORE_REPLACEMENT}\n"


def test_extract_steps():
    assert guards.extract_steps("說明\n1. 甲\n2）乙\n- 丙") == ["甲", "乙", "丙"]


# ── LOTO 先行 ───────────────────────────────────────────────────
def test_tool_guard_table():
    for st in ("OPEN", "ASSIGNED", "ESCALATED", "ACKED"):
        with pytest.raises(guards.ToolDenied):
            guards.check_tool("get_sop", {"failure_mode_id": "x", "phase": "inspect"}, st)
        with pytest.raises(guards.ToolDenied):
            guards.check_tool("get_machine_signals", {"signal": "feed_rate"}, st)
    guards.check_tool("get_loto", {}, "ACKED")
    guards.check_tool("get_sop", {"failure_mode_id": "x", "phase": "repair"}, "LOTO_DONE")
    with pytest.raises(guards.ToolDenied):                     # 作業許可（M2）開啟時，LOTO_DONE 仍受限
        guards.check_tool("get_sop", {"failure_mode_id": "x", "phase": "repair"}, "LOTO_DONE", require_permit=True)
    assert "set_status" not in guards.ALL_TOOLS and not any("status" in t for t in guards.ALL_TOOLS)


async def test_agent_loto_first(rt, fake_llm):
    wo = await open_feed_jam(rt)
    await rt.workorders.ack(wo.id, "P-STP-TC1")
    llm_before = len(fake_llm.calls)
    reply = await rt.agent.handle(wo.id, "卡料了，要怎麼處理？", "P-STP-TC1")
    assert reply.phase == "loto"
    assert "LOTO-FEED-01" in reply.content and "☐ 1." in reply.content
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
    await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")
    reply = await rt.agent.handle(wo.id, "LOTO 完成了，下一步？", "P-STP-TC1")
    assert reply.phase == "repair" and reply.llm_used and reply.hypothesis == "feed_roller_wear"
    # 復電指示被換掉
    assert "復電試運轉" not in reply.content and guards.RESTORE_REPLACEMENT in reply.content
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
    await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")
    fake_llm.fail = True
    reply = await rt.agent.handle(wo.id, "下一步？", "P-STP-TC1")
    assert not reply.llm_used and "SOP 原文" in reply.content and "目視滾輪" in reply.content


async def test_closeout_flow(rt):
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")
    items = rt.kb.checklist_for("feed_roller_wear")
    assert items == ["無料空轉量測送料誤差", "試沖 20 模"]
    for it in items:
        await rt.workorders.check_item(wo.id, it, "P-STP-TC1", items)
    assert rt.workorders.get(wo.id).status == "PENDING_VERIFY"
    bad = await submit_report(rt, wo.id, "P-STP-TC1", "修好了")
    assert bad["ok"] is False and rt.workorders.get(wo.id).status == "PENDING_VERIFY"
    assert bad["check"]["missing"] and bad["check"]["kb_status"] == "rejected"
    good = await submit_report(rt, wo.id, "P-STP-TC1", "下滾輪亮帶加油污，清潔無效後更換上下滾輪，送料誤差恢復 0.02 mm")
    assert good["ok"] and good["status"] == "CLOSED" and good["case_draft"] == f"CASE-DRAFT-{wo.id}.json"
    draft = rt.settings.path("drafts") / "stamping" / good["case_draft"]
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
