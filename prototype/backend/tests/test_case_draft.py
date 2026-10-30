"""結案案例由 LLM 整理（CL-3）：先寫程式版草稿、背景 LLM 整理；LLM 失敗、逾時、回覆不合格都退回程式版。"""
from __future__ import annotations

import json

import pytest
from sqlmodel import select

from sentinel.db.models import AuditLog
from sentinel.dispatch.closeout import submit_report
from sentinel.llm import case_draft
from test_agent import loto_done, open_feed_jam

REPORT = "下滾輪亮帶加油污，清潔無效後更換上下滾輪，無料空轉送料誤差恢復 0.02 mm，試沖 20 模正常"

LLM_REPLY = json.dumps({
    "technician_report_normalized": "送料機卡料，確認是送料滾輪磨損。清潔無效後更換上下滾輪，送料誤差恢復 0.02 mm。",
    "discriminating_evidence": ["下滾輪有亮帶和油污", "清潔後仍無效"],
    "actions": [{"step": "上鎖掛牌後清潔下滾輪", "result": "無效"},
                {"step": "更換上下滾輪", "result": "完成"},
                {"step": "無料空轉量測送料誤差、試沖 20 模", "result": "誤差 0.02 mm，試沖正常"}],
    "lessons": "滾輪有亮帶時清潔通常沒用，直接檢查磨耗。",
    "why_first_guess_wrong": None,
}, ensure_ascii=False)


async def close_with_report(rt, report: str = REPORT) -> dict:
    wo = await open_feed_jam(rt)
    await loto_done(rt, wo.id)
    items = rt.kb.checklist_for("feed_roller_wear")
    for it in items:
        await rt.workorders.check_item(wo.id, it, "P-STP-TC1", items)
    return await submit_report(rt, wo.id, "P-STP-TC1", report)


def audits(rt, action: str) -> list[dict]:
    return [a.detail for a in rt.db.all(select(AuditLog).where(AuditLog.action == action))]


def case_calls(fake_llm) -> list[dict]:
    return [c for c in fake_llm.calls if c["messages"][0]["content"].startswith("你是 Sentinel AI 的知識庫整理員")]


async def test_case_draft_llm_success(rt, fake_llm):
    fake_llm.case_reply, fake_llm.case_delay_s = LLM_REPLY, 0.05
    res = await close_with_report(rt)
    assert res["ok"] and res["status"] == "CLOSED"
    case_id = res["case_draft"]
    first = rt.writeback.get(case_id)                         # 結案當下：程式版，LLM 還在整理（不擋結案）
    assert first["drafted_by"] == "rules" and first["llm_draft"] == {"status": "pending"}
    assert first["technician_report_normalized"] == REPORT
    await rt.bus.drain()

    doc = rt.writeback.get(case_id)
    assert doc["drafted_by"] == "llm" and doc["llm_draft"]["status"] == "ok"
    assert doc["llm_draft"]["prompt_ver"] == case_draft.PROMPT_VER
    assert doc["technician_report_raw"] == REPORT              # 原文保留
    assert doc["technician_report_normalized"].startswith("送料機卡料")
    assert doc["discriminating_evidence"] == ["下滾輪有亮帶和油污", "清潔後仍無效"]
    assert doc["actions"][0] == {"step": "上鎖掛牌後清潔下滾輪", "result": "無效"}
    assert doc["actions"][-1]["result"] == "誤差 0.02 mm，試沖正常"
    assert doc["lessons"].startswith("滾輪有亮帶") and doc["review_status"] == "draft"

    # 提示詞：只整理、不新增；回報與確認的根因（中文名稱）都有送進去
    call = case_calls(fake_llm)[-1]
    assert "不可以新增事實、數值" in call["messages"][0]["content"]
    assert REPORT in call["messages"][1]["content"] and "送料滾輪磨損／打滑" in call["messages"][1]["content"]
    assert call["think"] is False

    assert audits(rt, "case_draft_written")[-1].items() >= {"drafted_by": "rules", "llm": "pending"}.items()
    llm_audit = audits(rt, "case_draft_llm")[-1]
    assert llm_audit["ok"] is True and llm_audit["drafted_by"] == "llm"

    # 主管核准前看得到草稿全文；核准後入庫的是 LLM 版
    row = next(c for c in rt.writeback.list("draft") if c["case_id"] == case_id)
    assert row["drafted_by"] == "llm" and row["content"]["actions"] == doc["actions"]
    approved, n = rt.writeback.approve(case_id, reviewer="P-STP-SV1", at=rt.clock.now())
    assert n == 2 and approved["drafted_by"] == "llm"


async def test_case_draft_llm_down_falls_back(rt, fake_llm):
    fake_llm.fail = True
    res = await close_with_report(rt)
    assert res["ok"] and res["status"] == "CLOSED"            # LLM 掛了也照常結案
    await rt.bus.drain()
    doc = rt.writeback.get(res["case_draft"])
    assert doc["drafted_by"] == "rules" and doc["llm_draft"]["status"] == "failed"
    assert "LLM 不可用" in doc["llm_draft"]["reason"]
    assert doc["technician_report_normalized"] == REPORT and doc["review_status"] == "draft"
    a = audits(rt, "case_draft_llm")[-1]
    assert a["ok"] is False and a["drafted_by"] == "rules"


@pytest.mark.parametrize("reply,why", [
    ("好的，以下是整理結果：送料滾輪磨損，已更換。", "不是 JSON"),
    ('{"technician_report_normalized": "更換滾輪", "actions": [', "不是 JSON"),           # 輸出被截斷
    ('{"technician_report_normalized": "更換滾輪", "actions": [}', "JSON 解析失敗"),
    ('{"technician_report_normalized": "", "actions": []}', "缺少摘要"),
    (LLM_REPLY.replace("0.02 mm，試沖正常", "0.01 mm，試沖 50 模正常"), "資料裡沒有的數值"),
])
async def test_case_draft_unparseable_falls_back(rt, fake_llm, reply, why):
    fake_llm.case_reply = reply
    res = await close_with_report(rt)
    await rt.bus.drain()
    doc = rt.writeback.get(res["case_draft"])
    assert doc["drafted_by"] == "rules" and doc["llm_draft"]["status"] == "failed"
    assert why in doc["llm_draft"]["reason"]
    assert doc["technician_report_normalized"] == REPORT and "actions" not in doc
    assert audits(rt, "case_draft_llm")[-1]["ok"] is False


async def test_case_draft_timeout_falls_back(rt, fake_llm):
    rt.settings.llm.case_draft_timeout_s = 0.05
    fake_llm.case_reply, fake_llm.case_delay_s = LLM_REPLY, 1.0
    res = await close_with_report(rt)
    await rt.bus.drain()
    doc = rt.writeback.get(res["case_draft"])
    assert doc["drafted_by"] == "rules" and doc["llm_draft"].items() >= {"status": "failed", "reason": "timeout"}.items()


async def test_case_draft_approved_before_llm_keeps_reviewed_version(rt, fake_llm):
    """主管在 LLM 整理完之前就核准：入庫的是主管看到的程式版，LLM 版不採用。"""
    fake_llm.case_reply, fake_llm.case_delay_s = LLM_REPLY, 0.1
    res = await close_with_report(rt)
    rt.writeback.approve(res["case_draft"], reviewer="P-STP-SV1", at=rt.clock.now())
    await rt.bus.drain()
    doc = rt.writeback.get(res["case_draft"])
    assert doc["review_status"] == "approved" and doc["drafted_by"] == "rules"
    assert "LLM 版不採用" in audits(rt, "case_draft_llm")[-1]["reason"]


async def test_case_draft_skipped_when_llm_disabled(rt, fake_llm):
    rt.llm.enabled = False
    res = await close_with_report(rt)
    await rt.bus.drain()
    doc = rt.writeback.get(res["case_draft"])
    assert doc["drafted_by"] == "rules" and doc["llm_draft"]["status"] == "skipped"
    assert case_calls(fake_llm) == [] and audits(rt, "case_draft_llm") == []


def test_case_draft_parse_rules():
    src = "回報：滾輪磨損，誤差 0.24 mm，更換後試沖 20 模"
    reply = ("```json\n" + json.dumps({"technician_report_normalized": "依 LOTO-FEED-01 驗證零能量後更換滾輪",
                                        "discriminating_evidence": "誤差 0.24 mm",
                                        "actions": ["更換滾輪", {"step": "試沖 20 模", "result": "正常"}],
                                        "lessons": "", "why_first_guess_wrong": ""}, ensure_ascii=False) + "\n```")
    out = case_draft.parse(reply, src)
    assert out["technician_report_normalized"] == "依上鎖掛牌確認機台不會動後更換滾輪"
    assert out["discriminating_evidence"] == ["誤差 0.24 mm"]
    assert out["actions"] == [{"step": "更換滾輪", "result": ""}, {"step": "試沖 20 模", "result": "正常"}]
    assert out["why_first_guess_wrong"] is None
    with pytest.raises(case_draft.CaseDraftInvalid, match="0.3"):
        case_draft.parse(reply.replace("0.24", "0.3"), src)
