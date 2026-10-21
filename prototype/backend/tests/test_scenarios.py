"""劇本重播（端到端）：假 Jev／LLM／偵測器，檢查每個劇本的 expect 是否達成。"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fakes import FakeJev
from sqlmodel import select

from sentinel.db.models import Diagnosis, EventRow, Incident, JevCall, Notification, Workorder
from sentinel.dispatch.notifier import ConsoleNotifier
from sentinel.runtime import Runtime


def sim_seconds(rt: Runtime, ts) -> float:
    return (ts - rt.settings.app.sim_start).total_seconds()


async def replay(rt: Runtime, scenario_id: str):
    sc = rt.scenarios[scenario_id]
    await rt.engine.run_stepped(sc)
    return sc


def check_expect(rt: Runtime, sc) -> tuple[Incident, Workorder]:
    exp = sc.expect
    incs = rt.db.all(select(Incident).where(Incident.type == exp.incident.type))
    assert len(incs) == 1, f"應該只有一個 {exp.incident.type} Incident（去重），實際 {len(incs)}"
    inc = incs[0]
    assert inc.final_severity == exp.incident.severity
    if exp.incident.category:
        assert inc.category == exp.incident.category
    opened = sim_seconds(rt, inc.opened_at)
    assert exp.t0 <= opened <= exp.t0 + exp.incident.within_s, f"Incident 在 t={opened}s 開立"
    wo = rt.workorders.for_incident(inc.id)
    assert wo is not None, "應該建立工單"
    if exp.assignee_role:
        assert wo.assignee_role == exp.assignee_role
    note = rt.db.first(select(Notification).where(Notification.workorder_id == wo.id, Notification.kind == "dispatch"))
    assert note is not None, "應該送出派工通知"
    assert sim_seconds(rt, note.ts) <= exp.t0 + exp.notify_within_s
    return inc, wo


async def test_feed_jam(rt: Runtime, fake_jev, fake_llm):
    sc = await replay(rt, "feed_jam")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-STP-FEEDJAM"
    assert inc.facts["source"] == "fused"
    assert inc.facts["feed_rate_before"] >= 40
    assert len(inc.facts["p_jam_last2"]) == 2 and min(inc.facts["p_jam_last2"]) > 0.7
    assert wo.status == "ASSIGNED" and wo.assignee_id == "P-STP-TC1"
    assert wo.cc == ["P-STP-LD1"]
    # Jev：看圖（定時＋Log 異常加送）＋事件判斷
    calls = rt.db.all(select(JevCall))
    purposes = {c.purpose for c in calls}
    assert {"vision.feed", "judge.event"} <= purposes
    triggers = [e.payload["trigger"] for e in rt.db.all(select(EventRow).where(EventRow.type == "jev_vision"))]
    assert "interval" in triggers and any(t.startswith("on_demand") for t in triggers)
    diag = rt.db.first(select(Diagnosis).where(Diagnosis.incident_id == inc.id))
    assert diag.mode == "jev_top3"
    assert diag.root_cause_top3[0]["id"] == "feed_roller_wear"
    assert diag.sop_ids == ["SOP-STP-012"]
    assert diag.explanation and "滾輪" in diag.explanation          # LLM 說明非同步補上
    notes = rt.db.all(select(Notification).where(Notification.workorder_id == wo.id))
    kinds = [n.kind for n in notes]
    assert kinds[0] == "dispatch" and "explanation" in kinds
    assert "根因前三名" in notes[0].text and "SOP-STP-012" in notes[0].text
    assert len(fake_llm.calls) == 1
    # 稽核鏈：同一個 trace_id 從規則命中到通知
    actions = [a.action for a in rt.audit.timeline(inc.trace_id)]
    for a in ("rule_hit", "routed", "jev_judgement", "workorder_created", "notified", "llm_explanation"):
        assert a in actions


async def test_ppe_violation(rt: Runtime, fake_llm):
    sc = await replay(rt, "ppe_violation")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-ASM-PPE" and inc.camera_id == "CAM-ASM-01"
    assert wo.assignee_id == "P-ASM-LD1"
    assert len(fake_llm.calls) == 0, "工安類不叫 LLM"
    assert rt.dispatch.diagnosis_for(inc.id).mode == "rules_only"


async def test_log_mismatch(rt: Runtime):
    sc = await replay(rt, "log_mismatch")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-STP-LOG-MISMATCH"
    assert inc.facts["vision_state"] == "idle" and inc.facts["log_state"] == "running"


async def test_feed_jam_without_jev(settings, clock, fake_llm):
    """Jev 逾時：規則改用「Log 送料速率歸零＋動態分數歸零」照常派工，通知註明 AI 暫不可用。"""
    notifier = ConsoleNotifier()
    rt = Runtime(settings, clock=clock, notifier=notifier, jev_transport=httpx.MockTransport(FakeJev(fail=True)),
                 llm_transport=httpx.MockTransport(fake_llm), jev_key="k", llm_key="k", use_vector_store=False)
    await rt.start(realtime=False, warmup=False)
    try:
        sc = await replay(rt, "feed_jam")
        inc, wo = check_expect(rt, sc)
        assert inc.facts["source"] == "mes+motion" and inc.facts["jev_available"] is False
        dispatch_msg = next(m for _, m in notifier.sent if not m.reply_to)
        assert "AI 判斷暫不可用" in dispatch_msg.text
        assert rt.db.all(select(JevCall).where(JevCall.error.is_not(None)))
    finally:
        await rt.stop()


async def test_jev_false_alarm_suppresses_notification(settings, clock, fake_llm):
    rt = Runtime(settings, clock=clock, notifier=ConsoleNotifier(),
                 jev_transport=httpx.MockTransport(FakeJev(clock.monotonic, p_false_alarm=0.95)),
                 llm_transport=httpx.MockTransport(fake_llm), jev_key="k", llm_key="k", use_vector_store=False)
    await rt.start(realtime=False, warmup=False)
    try:
        await replay(rt, "feed_jam")
        inc = rt.db.first(select(Incident))
        wo = rt.workorders.for_incident(inc.id)
        assert inc.suspected_false_alarm and wo.status == "OPEN"
        assert not rt.db.all(select(Notification))
    finally:
        await rt.stop()


async def test_escalation(rt: Runtime):
    rt.settings.escalation.demo_ack_timeout_s = 5
    sc = rt.scenarios["feed_jam"]
    await rt.engine.run_stepped(sc, duration_s=sc.expect.t0 + 7)
    wo = rt.db.first(select(Workorder))
    assert wo.escalation_level == 1 and wo.assignee_role == "leader" and wo.status == "ASSIGNED"
    assert "P-STP-TC1" in wo.cc
    trs = [(t.from_status, t.to_status) for t in rt.workorders.transitions(wo.id)]
    assert ("ASSIGNED", "ESCALATED") in trs and ("ESCALATED", "ASSIGNED") in trs
    assert rt.db.first(select(Notification).where(Notification.kind == "escalation"))


async def test_weld_smoke_s3_emergency(rt: Runtime, fake_llm):
    sc = await replay(rt, "weld_smoke")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-WLD-FIRE" and inc.machine_id == "WLD-L1-RC01" and inc.camera_id == "CAM-WLD-01"
    assert inc.facts["smoke"] == "outside_cell" and inc.facts["robot_state"] == "stopped"
    note = rt.db.first(select(Notification).where(Notification.workorder_id == wo.id, Notification.kind == "dispatch"))
    assert note.channel == "emergency" and "S3" in note.text
    assert wo.assignee_id == "P-WLD-MG1" and {"P-WLD-TC1", "P-WLD-SV1"} <= set(wo.cc)
    assert len(fake_llm.calls) == 0, "工安 S3 走緊急應變，不叫 LLM"
    # 煙霧出現前（機器人剛停止）不能誤報
    assert sim_seconds(rt, inc.opened_at) >= 8


async def test_screw_torque_s2_ai(rt: Runtime, fake_jev, fake_llm):
    sc = await replay(rt, "screw_torque")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-ASM-SCREW-NOK" and inc.machine_id == "ASM-L1-ST01"
    assert inc.facts["calib_overdue"] is True and inc.facts["nok_rate_pct"] > 3
    assert wo.assignee_id == "P-ASM-TC1"
    diag = rt.dispatch.diagnosis_for(inc.id)
    assert diag.mode == "llm_deep"                      # 測試知識庫沒有組裝故障模式 → Jev 不問根因，交給 LLM 深入說明
    judge_calls = [c for c in fake_jev.calls if "category" in c["questions"]]
    assert judge_calls and judge_calls[-1]["state"]["event"]["facts"]["calib_overdue"] is True
    assert len(fake_llm.calls) == 1


@pytest.mark.parametrize("sid", ["feed_jam", "ppe_violation", "log_mismatch", "weld_smoke", "screw_torque",
                                 "person_zone", "air_low"])
def test_scenarios_load(sid, settings):
    from sentinel.sim.scenario import load_scenarios

    sc = load_scenarios(settings.path("scenarios"))[sid]
    assert sc.expect.incident is not None


REAL_KB = Path(__file__).resolve().parents[2] / "knowledge"


@pytest.mark.skipif(not (REAL_KB / "failure_modes" / "assembly.yaml").exists(), reason="正式知識庫尚未建立")
async def test_screw_torque_with_real_catalog(tmp_path, clock, fake_llm):
    """用正式故障模式目錄：J3 根因選項只包含電動起子鎖附站的模式。"""
    from sentinel.config import BACKEND_DIR, load_settings

    s = load_settings(BACKEND_DIR / "config.yaml", paths={"db": str(tmp_path / "r.db"), "knowledge": str(REAL_KB),
                                                          "keyframes": str(tmp_path / "kf"), "drafts": str(tmp_path / "d")},
                      kb={"embedding": "hash", "allow_draft": True}, jev={"warmup": False})
    jev = FakeJev(clock.monotonic)
    rt = Runtime(s, clock=clock, notifier=ConsoleNotifier(), jev_transport=httpx.MockTransport(jev),
                 llm_transport=httpx.MockTransport(fake_llm), jev_key="k", llm_key="k", use_vector_store=False)
    await rt.start(realtime=False, warmup=False)
    try:
        await replay(rt, "screw_torque")
        inc = rt.db.first(select(Incident))
        rc_q = next(c for c in jev.calls if "root_cause" in c["questions"])["questions"]["root_cause"]["criteria"]
        assert "tool_calibration_due" in rc_q and "cable_pinch_misroute" not in rc_q    # 只限 screwdriving_station
        assert rt.dispatch.diagnosis_for(inc.id).root_cause_top3
    finally:
        await rt.stop()


async def test_person_zone_s3_and_8h_report(rt: Runtime, fake_llm):
    from datetime import timedelta

    from sentinel.db.models import IncidentReport
    from sentinel.dispatch.reports import SCENE_NOTICE

    sc = await replay(rt, "person_zone")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-STP-PERSON-ZONE" and inc.facts["light_curtain"] == {"STP-L1-PR01": "MUTED"}
    assert inc.casualty["hospitalized"] == 1 and inc.casualty["set_by"] == "P-STP-SV1"
    rep = rt.db.first(select(IncidentReport))
    assert rep.deadline == inc.opened_at + timedelta(hours=8) and rep.submitted_at is None   # 系統不自動送出
    assert rep.draft["現場保全"] == SCENE_NOTICE and "罹災需住院" in rep.draft["符合通報條件"]
    casualty_msg = rt.db.first(select(Notification).where(Notification.kind == "casualty"))
    assert SCENE_NOTICE in casualty_msg.text
    for who in ("職安人員", "急救人員", "課長"):
        assert who in casualty_msg.text
    st = rt.reports.status(inc, rep)
    assert st["status"] == "draft" and 0 < st["remaining_s"] <= 8 * 3600
    countdown = [e for e in rt.db.all(select(EventRow).where(EventRow.type == "report_countdown"))]
    assert countdown and countdown[0].payload["remaining_s"] > 0
    out = await rt.reports.submit(inc.id, "P-STP-SV1")
    assert out["status"] == "submitted" and out["submitted_by"] == "P-STP-SV1"


async def test_s3_without_casualty_has_no_report(rt: Runtime):
    from sentinel.db.models import IncidentReport

    await replay(rt, "weld_smoke")                      # S3 疑似火災，但沒有人標記傷亡
    assert rt.db.first(select(IncidentReport)) is None
    inc = rt.db.first(select(Incident))
    _, rep = await rt.reports.set_casualty(inc.id, injured=1, hospitalized=0, actor="P-WLD-SV1")
    assert rep is None                                  # 1 人輕傷不住院：不符合第37條第2項
    _, rep = await rt.reports.set_casualty(inc.id, injured=3, actor="P-WLD-SV1")
    assert rep is not None                              # 罹災 3 人以上 → 通報


async def test_air_low_multi_stop_s3(rt: Runtime, fake_jev, fake_llm):
    fake_jev.jam_from_s = 999                           # 假 Jev 依時間判卡料；這個劇本送料口畫面正常
    sc = await replay(rt, "air_low")
    inc, wo = check_expect(rt, sc)
    assert inc.rule_id == "R-PLANT-MULTI-STOP" and inc.facts["groups_stopped"] == 2
    assert set(inc.facts["air_pressure_low"]) >= {"STP-L1-PR01", "STP-L2-PR03"}
    assert [i.type for i in rt.db.all(select(Incident))] == ["multi_stop"]
    assert len(fake_llm.calls) == 1                     # 設備類 S3：緊急＋AI


async def test_feed_jam_interlocked_pair_is_not_multi_stop(rt: Runtime):
    await replay(rt, "feed_jam")                        # 送料機與沖床同一連動群組一起停 → 只有 S2 卡料
    types = [i.type for i in rt.db.all(select(Incident))]
    assert types == ["feed_jam"]
