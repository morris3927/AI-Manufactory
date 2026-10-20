"""單元測試：規則、路由、狀態機、Jev 回傳解析、影像工具、知識庫入庫守門。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fakes import JEV_SAMPLE

from sentinel.clock import TW, ManualClock
from sentinel.config import Thresholds
from sentinel.db.models import Incident
from sentinel.db.seed import seed_all
from sentinel.db.session import Database
from sentinel.dispatch.workorder import TRANSITIONS, WoStatus, can_transition
from sentinel.jev.client import BoxAnswer, ChoiceAnswer, NoulAnswer, ScoreAnswer, parse_response, summarize_state
from sentinel.jev.gate import decide
from sentinel.jev.judge import EventJudgement, RootCause, score_to_severity
from sentinel.jev.vision import MAX_PIXELS, MAX_SIDE, MIN_PIXELS, MIN_SIDE, fit_size, prepare_image, to_part
from sentinel.kb.ingest import collect_chunks, guard
from sentinel.rules.engine import load_rules
from sentinel.rules.routing import next_role, route
from sentinel.rules.windows import Windows
from sentinel.schemas.events import Event, Topic
from sentinel.schemas.plant import Plant
from sentinel.sim.roster import Roster
from sentinel.vision.keyframe import KeyframeScheduler
from sentinel.vision.ppe import Detection, ViolationTracker, find_violations

START = datetime(2026, 10, 30, 9, 0, tzinfo=TW)


# ── Jev 回傳解析（Jev整合規劃.md 3.1 的範例）────────────────────
def test_parse_jev_sample():
    r = parse_response(JEV_SAMPLE)
    assert r.model == "typesafe/jev-1.14-20261014" and r.cost == pytest.approx(0.000019992)
    assert r.input_tokens == 476
    assert isinstance(r.answers["is_bug"], NoulAnswer) and r.noul("is_bug") == 0.96
    team = r.choice("team")
    assert isinstance(team, ChoiceAnswer) and team.choice == "payments" and team.top(2)[0] == ("payments", 0.78)
    urg = r.score("urgency")
    assert isinstance(urg, ScoreAnswer) and urg.score == 1.99 and urg.legend["2"] == "..."
    assert score_to_severity(urg.score) == "S3"           # Score 從 0 起算：1.99 → 第 2 級 → S3
    box = r.box("where")
    assert isinstance(box, BoxAnswer) and box.box.left == 0.58 and box.confidence == 0.93


def test_score_to_severity_zero_indexed():
    assert [score_to_severity(x) for x in (0, 0.4, 0.6, 1.0, 1.49, 2, 3.5)] == ["S1", "S1", "S2", "S2", "S2", "S3", "S3"]


def test_summarize_state_redacts_images():
    s = summarize_state({"roi_image": {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 4000}}})
    assert s["roi_image"].startswith("<image")


def _j(**kw) -> EventJudgement:
    base = dict(category="equipment", category_conf=0.9, p_false_alarm=0.05, severity_score=1.0, jev_severity="S2",
                root_cause_top3=[RootCause(id="a", label="a", p=0.7)], root_cause_conf=0.7)
    return EventJudgement(**(base | kw))


def test_gate_decide():
    assert decide("S2", _j()).model_dump() | {} == {"action": "dispatch", "severity": "S2", "category": "equipment",
                                                     "diagnosis_mode": "jev_top3", "reason": None}
    assert decide("S3", _j(jev_severity="S1")).severity == "S3"          # 不能降級
    assert decide("S2", _j(jev_severity="S3")).severity == "S3"          # 可以升級
    assert decide("S2", _j(p_false_alarm=0.95)).action == "log_only"
    assert decide("S3", _j(p_false_alarm=0.99)).action == "dispatch"      # S3 不能壓掉
    assert decide("S2", _j(root_cause_conf=0.4)).diagnosis_mode == "llm_deep"


# ── 影像 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("w,h", [(440, 310), (1280, 720), (100, 80), (3000, 200), (4000, 4000), (256, 256)])
def test_to_part_size_rules(w, h):
    img = prepare_image(np.zeros((h, w, 3), np.uint8), max_side=768)
    ih, iw = img.shape[:2]
    assert MIN_SIDE <= iw <= MAX_SIDE and MIN_SIDE <= ih <= MAX_SIDE
    assert MIN_PIXELS <= iw * ih <= MAX_PIXELS


def test_to_part_jpeg_roi():
    part = to_part(np.zeros((720, 1280, 3), np.uint8), [420, 310, 860, 620])
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert fit_size(440, 310)[:2] == (440, 310)


def test_ppe_overlap_rule():
    dets = [Detection("head", (0, 0, 100, 100), 0.9), Detection("helmet", (0, 0, 100, 40), 0.9),   # 重疊 0.4 → 有戴
            Detection("head", (300, 0, 400, 100), 0.9), Detection("helmet", (300, 0, 400, 20), 0.9)]  # 0.2 → 沒戴
    v = find_violations(dets, 0.3)
    assert len(v) == 1 and v[0].head_box == (300, 0, 400, 100) and v[0].missing == ["helmet"]
    tr = ViolationTracker(3.0)
    assert tr.should_emit(v[0], 0.0) and not tr.should_emit(v[0], 2.0) and tr.should_emit(v[0], 3.5)


def test_keyframe_scheduler():
    k = KeyframeScheduler(5)
    assert k.due("c", 0) == "interval" and k.due("c", 3) is None and k.due("c", 5) == "interval"
    k.request("c", "alarm")
    assert k.due("c", 6).startswith("on_demand")


# ── 規則 ────────────────────────────────────────────────────────
@pytest.fixture
def plant(settings) -> Plant:
    return Plant.load(settings.path("plant"))


def feed(w: Windows, clock: ManualClock, t: float, **vals):
    clock.set(t)
    w.ingest(Event(ts=clock.now(), topic=Topic.MES, source="t", machine_id="STP-L1-FD02", type="mes_log",
                   payload={"state": "running", **vals}))


def jev(w: Windows, clock: ManualClock, t: float, p: float):
    clock.set(t)
    w.ingest(Event(ts=clock.now(), topic=Topic.VISION, source="t", machine_id="STP-L1-FD02", camera_id="CAM-STP-01",
                   type="jev_vision", payload={"p_jam": p, "p_smoke": 0.0, "box": None}))


def test_rule_feed_jam_hits_only_with_both_sources():
    rules = load_rules()
    clock = ManualClock(START)
    w = Windows(clock, Thresholds())
    fn = rules["R-STP-FEEDJAM"].fn
    for t in range(0, 10):
        feed(w, clock, t, feed_rate=42)
    jev(w, clock, 10, 0.82)
    assert fn(w, "STP-L1-FD02") is None                      # 只有 Jev
    feed(w, clock, 11, feed_rate=0)
    assert fn(w, "STP-L1-FD02") is None                      # 只有 1 張 > 0.7
    jev(w, clock, 12, 0.88)
    f = fn(w, "STP-L1-FD02")
    assert f and f.type == "feed_jam" and f.facts["feed_rate_before"] == 42 and f.facts["p_jam_last2"] == [0.82, 0.88]


def test_rule_feed_jam_not_without_drop():
    clock = ManualClock(START)
    w = Windows(clock, Thresholds())
    for t in range(0, 15):
        feed(w, clock, t, feed_rate=42)
    jev(w, clock, 10, 0.9)
    jev(w, clock, 14, 0.9)
    assert load_rules()["R-STP-FEEDJAM"].fn(w, "STP-L1-FD02") is None


def test_rule_log_mismatch():
    clock = ManualClock(START)
    w = Windows(clock, Thresholds(log_mismatch_s=20))
    fn = load_rules()["R-STP-LOG-MISMATCH"].fn
    w.ingest(Event(ts=clock.now(), topic=Topic.VISION, source="t", machine_id="STP-L1-FD02", type="machine_state",
                   payload={"state": "idle"}))
    for t in range(0, 19):
        feed(w, clock, t, feed_rate=42)
    assert fn(w, "STP-L1-FD02") is None
    for t in range(19, 22):
        feed(w, clock, t, feed_rate=42)
    assert fn(w, "STP-L1-FD02").type == "log_mismatch"


def test_rule_applies_by_line_and_type(plant):
    rules = load_rules()
    assert rules["R-STP-FEEDJAM"].applies("stamping", "STP-L1-FD02", plant)
    assert not rules["R-STP-FEEDJAM"].applies("stamping", "STP-L1-PR01", plant)   # 機型不符
    assert rules["R-ASM-PPE"].applies("assembly", "CAM-ASM-01", plant)
    assert not rules["R-ASM-PPE"].applies("stamping", "CAM-STP-01", plant)


def test_windows_helpers():
    clock = ManualClock(START)
    w = Windows(clock)
    for t, v in enumerate([42, 42.1, 41.9, 42, 0]):
        clock.set(t)
        w.mes("m", "feed_rate").add(clock.now(), v)
    s = w.mes("m", "feed_rate")
    assert s.changed_within(2) and s.seconds_since_change() == 1.0 and s.max(10) == 42.1
    assert s.duration_true(lambda v: v == 0) == 0.0
    clock.set(4)
    assert not w.mes("m", "feed_rate").changed_within(0)


# ── 路由 ────────────────────────────────────────────────────────
@pytest.fixture
def roster(tmp_path, settings, plant) -> Roster:
    db = Database(tmp_path / "r.db")
    db.create_all()
    seed_all(db, plant, settings.path("roster"), settings.path("access"))
    return Roster(db)


def inc(category, sev, line="stamping") -> Incident:
    return Incident(id="I", trace_id="T", line=line, type="x", category=category, rule_id="R", rule_severity=sev,
                    final_severity=sev, opened_at=START)


def test_route_equipment_s2(roster):
    p = route(inc("equipment", "S2"), roster, START)
    assert (p.mode, p.ai, p.assignee_role, p.assignee_id, p.cc, p.channel) == \
        ("dispatch", True, "technician", "P-STP-TC1", ["P-STP-LD1"], "stamping")


def test_route_safety_s2(roster):
    p = route(inc("safety", "S2", "assembly"), roster, START)
    assert (p.mode, p.ai, p.assignee_id) == ("dispatch_only", False, "P-ASM-LD1")


def test_route_s3_emergency(roster):
    p = route(inc("equipment", "S3"), roster, START)
    assert p.mode == "emergency" and p.channel == "emergency" and p.assignee_role == "manager"
    assert "P-STP-TC1" in p.cc and "P-STP-SV1" in p.cc
    assert route(inc("equipment", "S1"), roster, START).mode == "log_only"


def test_route_access_reassign(roster):
    from sentinel.db.models import AccessRecord

    # TC1 打卡離廠、TC2 08:40 已離廠 → 改派同職位的 TC3
    roster.db.add(AccessRecord(person_id="P-STP-TC1", ts=START - timedelta(minutes=5), direction="out"))
    p = route(inc("equipment", "S2"), roster, START)
    assert p.assignee_id == "P-STP-TC3" and sum("不在廠" in n for n in p.notes) == 2
    # 技術員都不在廠 → 往上一級班長
    roster.db.add(AccessRecord(person_id="P-STP-TC3", ts=START - timedelta(minutes=1), direction="out"))
    p = route(inc("equipment", "S2"), roster, START)
    assert p.assignee_role == "leader" and any("改派班長" in n for n in p.notes)


def test_next_role():
    assert [next_role(r) for r in ("technician", "leader", "supervisor", "manager")] == \
        ["leader", "supervisor", "manager", None]


# ── 狀態機 ──────────────────────────────────────────────────────
def test_state_machine_table():
    S = WoStatus
    eq = [S.OPEN, S.ASSIGNED, S.ACKED, S.LOTO_DONE, S.IN_REPAIR, S.PENDING_VERIFY, S.CLOSED]
    for a, b in zip(eq, eq[1:]):
        assert can_transition(a, b, "equipment", "P1" if b != S.ASSIGNED else "system"), (a, b)
    assert not can_transition(S.ACKED, S.CLOSED, "equipment", "P1")     # 設備類不能跳過 LOTO
    assert can_transition(S.ACKED, S.CLOSED, "safety", "P1")            # 安全類短流程
    assert not can_transition(S.ACKED, S.LOTO_DONE, "safety", "P1")
    assert can_transition(S.ASSIGNED, S.ESCALATED, "equipment", "system")
    assert not can_transition(S.ASSIGNED, S.ESCALATED, "equipment", "P1")  # 只有系統會升級
    assert not can_transition(S.ASSIGNED, S.ACKED, "equipment", "system")  # 接單只能由人
    assert can_transition(S.ESCALATED, S.ASSIGNED, "equipment", "system")
    assert can_transition(S.OPEN, S.CANCELLED, "equipment", "P1")
    assert not can_transition(S.CLOSED, S.OPEN, "equipment", "P1")
    for (a, b) in TRANSITIONS:
        assert not can_transition(a, b, "equipment", "agent:WO-1") and not can_transition(a, b, "safety", "jev")


async def test_workorder_flow_and_permissions(rt):
    from sentinel.dispatch.workorder import PermissionDenied, TransitionError

    await rt.engine.run_stepped(rt.scenarios["feed_jam"], duration_s=16)
    wo = rt.db.first(__import__("sqlmodel").select(__import__("sentinel.db.models", fromlist=["Workorder"]).Workorder))
    with pytest.raises(PermissionDenied):
        await rt.workorders.transition(wo.id, "ACKED", actor="agent:" + wo.id)
    with pytest.raises(TransitionError):
        await rt.workorders.transition(wo.id, "CLOSED", actor="P-STP-TC1")
    await rt.workorders.ack(wo.id, "P-STP-TC1")
    loto = rt.agent.ctx.loto_for(wo.id)
    assert loto.loto_id == "LOTO-FEED-01"
    for s in loto.steps:
        wo, done = await rt.workorders.check_loto(wo.id, s.no, "P-STP-TC1", loto_id=loto.loto_id,
                                                  steps=[x.no for x in loto.steps])
    assert done and wo.status == "LOTO_DONE"
    with pytest.raises(PermissionDenied):
        await rt.workorders.permit(wo.id, "P-STP-TC1", "approve")        # 技術員不能核准
    wo = await rt.workorders.permit(wo.id, "P-STP-LD1", "approve")
    assert wo.status == "IN_REPAIR"
    trs = [t.to_status for t in rt.workorders.transitions(wo.id)]
    assert trs == ["OPEN", "ASSIGNED", "ACKED", "LOTO_DONE", "IN_REPAIR"]


# ── 知識庫 ──────────────────────────────────────────────────────
def test_kb_guard():
    assert guard({"license": "", "review_status": "approved"}) == "缺少 license"
    assert "©" in guard({"license": "©", "review_status": "approved"})
    assert guard({"license": "PD", "review_status": "draft"}) == "review_status=draft"
    assert guard({"license": "PD", "review_status": "draft"}, allow_draft=True) is None
    assert guard({"license": "PD", "review_status": "approved"}) is None


def test_kb_chunking(knowledge):
    chunks, rep = collect_chunks(knowledge)
    assert ("SOP-STP-099", "review_status=draft") in rep.rejected
    ids = {c["chunk_id"] for c in chunks["docs"]}
    assert {"SOP-STP-012#summary", "SOP-STP-012#inspect", "SOP-STP-012#verify"} <= ids
    assert {c["chunk_id"] for c in chunks["cases"]} == {"CASE-STP-0001#summary", "CASE-STP-0001#detail"}
    assert all(c["metadata"]["license"] == "internal-synthetic" for c in chunks["docs"] if c["metadata"]["doc_type"] == "sop")
    chunks2, _ = collect_chunks(knowledge, allow_draft=True)
    assert any(c["chunk_id"].startswith("SOP-STP-099") for c in chunks2["docs"])


def test_kb_empty_knowledge(tmp_path):
    from sentinel.kb.retrieve import DEFAULT_LOTO, KnowledgeBase

    kb = KnowledgeBase(tmp_path / "nothing", None)
    assert kb.failure_modes == {} and kb.sops == {} and kb.loto_for("servo_press") is DEFAULT_LOTO
    assert kb.plan_sops([])["mode"] == "no_root_cause"


def test_kb_sop_by_id_and_plan(rt):
    kb = rt.kb
    assert [c.phase for c in kb.sops_for(["feed_roller_wear"], phases=["inspect"])] == ["inspect"]
    assert kb.plan_sops([{"id": "feed_roller_wear", "p": 0.7}])["mode"] == "top1_all_phases"
    assert kb.plan_sops([{"id": "feed_roller_wear", "p": 0.4}, {"id": "x", "p": 0.3}])["mode"] == "top3_inspect"
    assert kb.plan_sops([{"id": "new", "p": 0.9}])["mode"] == "semantic"
    inc_ = inc("equipment", "S2")
    inc_.facts = {"feed_rate_now": 0}
    cases = kb.similar_cases(inc_, machine_type="servo_roll_feeder")
    assert cases and cases[0].case_id == "CASE-STP-0001" and cases[0].hypothesis_correct is False


# ── 匯流 ────────────────────────────────────────────────────────
async def test_bus_drops_oldest_frames_only():
    from sentinel.bus import EventBus

    persisted = []
    bus = EventBus(persist=persisted.append)
    q = bus.subscribe(Topic.FRAMES, "t", maxsize=2)
    for i in range(5):
        await bus.publish(Event(ts=START, topic=Topic.FRAMES, source="t", type="frame", payload={"i": i}))
    assert [q.get_nowait().payload["i"] for _ in range(2)] == [3, 4]
    assert persisted == []                                              # frames 不寫資料庫
    await bus.publish(Event(ts=START, topic=Topic.MES, source="t", type="mes_log"))
    assert len(persisted) == 1


# ── 正式知識庫（prototype/knowledge/，由另一組維護）────────────
REAL_KB = Path(__file__).resolve().parents[2] / "knowledge"


@pytest.mark.skipif(not (REAL_KB / "failure_modes").exists(), reason="正式知識庫尚未建立")
def test_real_knowledge_guard_and_heldout():
    from sentinel.eval.testset import heldout_cases
    from sentinel.kb.ingest import load_cases
    from sentinel.kb.retrieve import KnowledgeBase

    chunks, rep = collect_chunks(REAL_KB)                       # 預設只收 approved
    assert all(c["metadata"].get("review_status") == "approved"
               for c in chunks["docs"] + chunks["cases"])
    chunks, _ = collect_chunks(REAL_KB, allow_draft=True)
    held = {c["case_id"] for c in heldout_cases(REAL_KB)}
    assert held and not held & {c["metadata"]["case_id"] for c in chunks["cases"]}   # heldout 永遠不入庫
    assert not held & {c["case_id"] for c in load_cases(REAL_KB)}
    kb = KnowledgeBase(REAL_KB, None, allow_draft=True)
    loto = kb.loto_for("servo_roll_feeder")
    assert not loto.is_default and [s.no for s in loto.steps][:3] == [1, 2, 3]          # "no" key 讀得到
    assert kb.failure_modes_for("stamping", "servo_roll_feeder")
    assert kb.severity_notes("stamping")


def test_mes_signals_and_events(settings):
    import asyncio

    from sentinel.bus import EventBus
    from sentinel.sim.mes import MesSimulator

    plant = Plant.load(settings.path("plant"))
    assert {"STP-L2-PR03", "STP-L2-FD04"} <= set(plant.machines)
    clock = ManualClock(START)
    bus = EventBus()
    q = bus.subscribe(Topic.MES, "t")
    sim = MesSimulator(plant, bus, clock)
    sim.load_script({"STP-L2-PR03": [{"t": 1, "event": {"type": "coil_change", "payload": {"coil_id": "C-1"}}}]})

    async def run():
        for t in (0, 1, 2):
            clock.set(t)
            await sim.tick(t)

    asyncio.run(run())
    evts = [q.get_nowait() for _ in range(q.qsize())]
    press = [e for e in evts if e.machine_id == "STP-L2-PR03" and e.type == "mes_log"]
    assert {"stop_angle_deg", "air_pressure_bar", "light_curtain_status", "stroke_count_since_regrind"} <= set(press[0].payload)
    assert press[-1].payload["stroke_count_since_regrind"] > press[0].payload["stroke_count_since_regrind"]
    assert [e.payload for e in evts if e.type == "coil_change"] == [{"coil_id": "C-1"}]
