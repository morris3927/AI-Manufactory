"""前端契約測試（prototype/frontend/lib/api.ts、lib/ws.ts、lib/types.ts）：用 TestClient 走真的路由。"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from fakes import FakeJev, FakeLlm

from sentinel.clock import ManualClock
from sentinel.dispatch.notifier import ConsoleNotifier
from sentinel.kb.ingest import ingest
from sentinel.main import create_app
from sentinel.runtime import Runtime

TECH = {"X-Role": "technician", "X-Person-Id": "P-T01"}      # 前端的身分 ID（不在 roster）→ 依角色對應
LEADER = {"X-Role": "leader", "X-Person-Id": "P-L01"}
SUP = {"X-Role": "supervisor", "X-Person-Id": "P-S01"}
JUDGE = {"X-Role": "judge", "X-Person-Id": "JUDGE"}


@pytest.fixture
def client(settings):
    clock = ManualClock(settings.app.sim_start)

    def factory() -> Runtime:
        rt = Runtime(settings, clock=clock, notifier=ConsoleNotifier(),
                     jev_transport=httpx.MockTransport(FakeJev(clock.monotonic)),
                     llm_transport=httpx.MockTransport(FakeLlm()), jev_key="k", llm_key="k")
        ingest(settings.path("knowledge"), rt.kb.store)
        return rt

    app = create_app(factory, start_kwargs={"realtime": False, "warmup": False})
    with TestClient(app) as c:
        yield c


def keys(d: dict, *names: str) -> None:
    missing = [n for n in names if n not in d]
    assert not missing, f"缺少欄位 {missing}"


def test_contract_feed_jam_flow(client: TestClient):
    plant = client.get("/api/plant").json()
    cams = [c for l in plant["lines"] for c in l["cameras"]]
    assert all("source" in c for c in cams)
    assert {s["id"] for s in client.get("/api/scenarios").json()} >= {"feed_jam", "ppe_violation"}
    assert client.get("/api/videos").json()
    assert client.post("/api/scenarios/feed_jam/run", json={}).json()["ok"]

    wos = client.get("/api/workorders").json()
    assert len(wos) == 1
    keys(wos[0], "id", "incident_id", "status", "line", "machine_id", "severity", "category", "title", "trace_id",
         "assignee_name", "assignee_role", "escalation_level", "ack_deadline")
    wid = wos[0]["id"]
    d = client.get(f"/api/workorders/{wid}").json()
    keys(d, "incident", "diagnosis", "sops", "loto", "permit", "checklist", "agent_messages", "transitions", "report")
    keys(d["incident"], "camera_id", "title", "jev_box", "keyframe_url", "diagnosis")
    assert d["incident"]["camera_id"] == "CAM-STP-01" and len(d["incident"]["jev_box"]) == 4
    assert d["incident"]["keyframe_url"].endswith(f"/api/incidents/{d['incident_id']}/keyframe")
    assert client.get(d["incident"]["keyframe_url"]).headers["content-type"] == "image/jpeg"
    rc = d["diagnosis"]["root_cause_top3"][0]
    keys(rc, "failure_mode_id", "name", "p")
    assert d["diagnosis"]["explanation_status"] == "done"
    keys(d["loto"]["steps"][0], "step_no", "text", "checked_by", "ts")
    keys(d["sops"][0], "sop_id", "title", "phase", "steps")
    assert d["transitions"][0]["from"] is None and d["transitions"][0]["to"] == "OPEN"

    assert client.post(f"/api/workorders/{wid}/ack", json={}, headers=JUDGE).status_code == 403
    r = client.post(f"/api/workorders/{wid}/ack", json={}, headers=TECH).json()
    assert r["status"] == "ACKED" and r["assignee_id"] == "P-STP-TC1"           # POST 回更新後的工單
    assert r["workflow"]["next_action"] == "permit_request"
    lo = client.post(f"/api/workorders/{wid}/loto/1", json={"checked": True}, headers=TECH)
    assert lo.status_code == 409 and "作業許可" in lo.json()["detail"]                # 許可前不能上鎖
    assert client.post(f"/api/workorders/{wid}/permit", json={"action": "approve"}, headers=LEADER).status_code == 409
    r = client.post(f"/api/workorders/{wid}/permit", json={"action": "request"}, headers=TECH).json()
    assert r["status"] == "PERMIT_REQUESTED" and r["permit"]["status"] == "requested"
    assert client.post(f"/api/workorders/{wid}/permit", json={"action": "approve"}, headers=TECH).status_code == 403
    r = client.post(f"/api/workorders/{wid}/permit", json={"action": "approve"}, headers=LEADER).json()
    assert r["status"] == "PERMIT_APPROVED" and r["permit"]["approved_by"] == "P-STP-LD1"
    assert r["workflow"]["next_action"] == "loto"
    for s in d["loto"]["steps"]:
        r = client.post(f"/api/workorders/{wid}/loto/{s['step_no']}", json={"checked": True}, headers=TECH).json()
    assert r["status"] == "IN_REPAIR"
    assert [t["to"] for t in r["transitions"]][-2:] == ["LOTO_DONE", "IN_REPAIR"]

    a = client.post(f"/api/workorders/{wid}/agent?wait=true", json={"message": "下一步？"}, headers=TECH).json()
    assert a["accepted"] and a["message_id"] and a["message"]["id"] == a["message_id"]
    assert a["message"]["role"] == "assistant" and a["message"]["content"] and a["message"]["safety_flags"]
    d = client.get(f"/api/workorders/{wid}").json()
    flagged = [m for m in d["agent_messages"] if m["safety_flags"]]
    assert flagged and keys(flagged[0]["safety_flags"][0], "step_index", "step_text", "probs", "max_p") is None
    assert client.post("/api/overrides", headers=TECH, json={"target": "workorder", "target_id": wid,
                                                              "field": "safety_ack", "new": flagged[0]["id"],
                                                              "reason": "x"}).status_code == 403
    ok = client.post("/api/overrides", headers=SUP, json={"target": "workorder", "target_id": wid, "field": "safety_ack",
                                                          "new": flagged[0]["id"], "reason": "改用取料夾具"})
    assert ok.json()["ok"]
    d = client.get(f"/api/workorders/{wid}").json()
    assert all(not f["supervisor_requested"] for m in d["agent_messages"] for f in m["safety_flags"])
    assert d["status"] == "IN_REPAIR"                                            # 覆寫不改工單狀態

    for it in d["checklist"]:
        r = client.post(f"/api/workorders/{wid}/checklist/{it['item']}", json={"checked": True}, headers=TECH).json()
    assert r["status"] == "PENDING_VERIFY"
    bad = client.post(f"/api/workorders/{wid}/report", headers=TECH, json={"text": "好了", "root_cause_confirmed": None}).json()
    keys(bad, "check", "workorder")
    assert bad["check"]["complete"] is False and bad["check"]["missing"]
    good = client.post(f"/api/workorders/{wid}/report", headers=TECH, json={
        "text": "下滾輪亮帶油污，更換上下滾輪後送料誤差恢復 0.02 mm", "root_cause_confirmed": "feed_roller_wear"}).json()
    assert good["check"]["complete"] is True and good["check"]["kb_status"] == "draft"
    assert good["workorder"]["status"] == "CLOSED" and good["workorder"]["report"]["text"]
    wb = good["workorder"]["writeback"]
    assert wb["status"] == "draft" and wb["approve_url"] == f"/api/kb/cases/{wb['case_id']}/approve"
    assert [c["case_id"] for c in client.get("/api/kb/cases?status=draft").json()] == [wb["case_id"]]
    assert client.post(wb["approve_url"], json={}, headers=LEADER).status_code == 403     # 需要組長以上
    ap = client.post(wb["approve_url"], json={}, headers=SUP).json()
    assert ap["ok"] and ap["case"]["status"] == "approved" and ap["case"]["reviewed_by"] == ["P-STP-SV1"]
    assert ap["ingested_chunks"] == 2
    assert client.get(f"/api/workorders/{wid}").json()["writeback"]["status"] == "approved"
    assert client.post(wb["approve_url"], json={}, headers=SUP).status_code == 409

    audit = client.get(f"/api/audit?workorder_id={wid}").json()
    assert {"rule_hit", "workorder_created", "notified"} <= {a["action"] for a in audit}
    m = client.get("/api/metrics/summary").json()
    keys(m, "reaction_s", "mttr_min", "ai_cost_usd", "ai_cost_series", "handled", "severity_counts", "comparison")
    keys(m["reaction_s"], "detect", "notify", "arrive")
    keys(m["handled"], "rules_only", "jev", "llm")
    cal = client.get("/api/metrics/calibration").json()
    keys(cal, "bins", "brier", "ece")
    assert cal["bins"][0].keys() >= {"p_pred", "p_obs", "n"}
    sig = client.get("/api/machines/STP-L1-FD02/signals").json()
    assert sig["signals"]["feed_rate"][0].keys() == {"ts", "v"}


def test_contract_settings_and_overrides(client: TestClient):
    s = client.post("/api/demo/settings", json={"escalation_timeout_s": 30, "jev_enabled": False}).json()
    assert s["escalation_timeout_s"] == 30 and s["jev_enabled"] is False
    assert client.get("/api/demo/settings").json().keys() >= {"escalation_timeout_s", "jev_enabled"}
    client.post("/api/scenarios/ppe_violation/run", json={})
    wid = client.get("/api/workorders").json()[0]["id"]
    r = client.post("/api/overrides", headers=LEADER, json={"target": "workorder", "target_id": wid,
                                                            "field": "final_severity", "new": "S3", "reason": "測試"})
    assert r.json()["ok"]
    assert client.get(f"/api/workorders/{wid}").json()["severity"] == "S3"
    r = client.post("/api/overrides", headers=LEADER, json={"target": "workorder", "target_id": wid,
                                                            "field": "false_alarm", "new": True, "reason": "現場確認誤報"})
    assert r.json()["ok"] and client.get(f"/api/workorders/{wid}").json()["status"] == "CANCELLED"
    assert client.post("/api/demo/reset", json={}).json()["ok"]
    assert client.get("/api/workorders").json() == []


def test_contract_incident_report(client: TestClient):
    client.post("/api/scenarios/person_zone/run", json={})
    inc = next(i for i in client.get("/api/incidents").json() if i["type"] == "person_in_zone")
    r = client.get(f"/api/incidents/{inc['id']}/report").json()
    keys(r, "incident_id", "required", "criteria_met", "casualty", "status", "deadline", "remaining_s", "draft",
         "instructions", "submitted_by", "submitted_at")
    assert r["status"] == "draft" and r["instructions"][0].startswith("非經司法機關")
    assert inc["incident_report"]["status"] == "draft"
    url = f"/api/incidents/{inc['id']}/report/submit"
    assert client.post(url, json={}, headers=TECH).status_code == 403
    done = client.post(url, json={"draft": {"傷亡概況": "1 人手部挫傷住院觀察（模擬）"}}, headers=SUP).json()
    assert done["status"] == "submitted" and done["draft"]["傷亡概況"].startswith("1 人")
    assert client.post(url, json={}, headers=SUP).status_code == 409
    # 沒有傷亡的 S3：主管標記 1 人輕傷 → 不需通報
    client.post("/api/scenarios/weld_smoke/run", json={})
    fire = next(i for i in client.get("/api/incidents").json() if i["type"] == "fire_suspected")
    c = client.post(f"/api/incidents/{fire['id']}/casualty", json={"injured": 1}, headers=SUP).json()
    assert c["required"] is False and c["status"] == "none"
    assert client.post(f"/api/incidents/{fire['id']}/casualty", json={"injured": 1}, headers=LEADER).status_code == 403


def test_contract_websocket(client: TestClient):
    with client.websocket_connect("/ws?role=technician&person_id=P-T01") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"action": "subscribe", "channels": ["workorders", "incidents"]})
        client.post("/api/scenarios/ppe_violation/run", json={})
        seen = []
        for _ in range(50):
            m = ws.receive_json()
            assert m["channel"] in ("workorders", "incidents") and {"channel", "type", "data", "ts"} <= m.keys()
            seen.append((m["channel"], m["type"]))
            if m["channel"] == "workorders" and m["type"] == "status_changed":
                assert m["data"]["workorder"]["id"] == m["data"]["id"]
                break
        assert ("incidents", "incident_opened") in seen and ("workorders", "created") in seen


def test_ws_message_mapping():
    from datetime import datetime

    from sentinel.api.ws import to_messages
    from sentinel.clock import TW
    from sentinel.schemas.events import Event, Topic

    ts = datetime(2026, 10, 30, 9, tzinfo=TW)
    msg = {"id": "7", "workorder_id": "WO-0001", "role": "assistant", "content": "1. 甲", "safety_flags": [], "ts": ""}
    out = to_messages(Event(ts=ts, topic=Topic.AI, source="agent", type="agent_reply",
                            payload={"workorder_id": "WO-0001", "message": msg, "flags": [{"step_index": 0}]}))
    assert [(m["channel"], m["type"]) for m in out] == [("agent:WO-0001", "delta"), ("agent:WO-0001", "message"),
                                                        ("agent:WO-0001", "safety_gate")]
    out = to_messages(Event(ts=ts, topic=Topic.TASKS, source="d", type="workorder_transition",
                            payload={"workorder_id": "WO-1", "to": "ESCALATED"}))
    assert out[0]["type"] == "escalated"
    out = to_messages(Event(ts=ts, topic=Topic.AI, source="jev", type="diagnosis", payload={"incident_id": "I"}))
    assert out[0]["type"] == "jev_result"
    out = to_messages(Event(ts=ts, topic=Topic.MES, source="sim.mes", machine_id="M", type="mes_log", payload={"spm": 1}))
    assert out[0]["channel"] == "events" and out[0]["data"]["payload"] == {"spm": 1}
