"""WebSocket /ws（08_系統設計.md §7.2；格式對齊 prototype/frontend/lib/ws.ts）。

連線：/ws?role=&person_id=，連上後送 {"action": "subscribe", "channels": [...]}（取消用 "unsubscribe"）。
還沒送訂閱前預設全收。訊息格式：{"channel", "type", "data", "ts"}。

| 頻道 | type | data |
|---|---|---|
| events | mes_log、machine_alarm、machine_state、jev_vision、ppe_violation、camera_source、incident_opened… | 完整事件 {event_id, ts, topic, source, line, machine_id, camera_id, type, payload, trace_id} |
| incidents | incident_opened、incident_updated | 同上 |
| workorders | created、status_changed、escalated、updated、reset | {id, workorder, ...} |
| ai | jev_vision、jev_result、llm_explanation、new_failure_mode | 事件 payload（含 workorder_id／incident_id） |
| agent:{id} | message、delta、safety_gate | AgentMessage／{message_id, text}／{message_id, flags} |
| metrics | summary | MetricsSummary（每 5 秒） |

LLM 回覆目前不是逐字串流：delta 會一次送出完整文字，接著送 message。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db.models import Workorder
from ..eval import metrics
from ..ids import jsonable, new_id
from ..schemas.events import Event, Topic

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)
router = APIRouter()

TOPICS = [Topic.VISION, Topic.MES, Topic.ALERTS, Topic.TASKS, Topic.AI]
WO_TYPE = {"workorder_created": "created", "reset": "reset"}


def event_dict(evt: Event) -> dict:
    return jsonable({"event_id": evt.event_id, "ts": evt.ts, "topic": str(evt.topic), "source": evt.source,
                     "line": evt.line, "machine_id": evt.machine_id, "camera_id": evt.camera_id, "type": evt.type,
                     "payload": evt.payload, "trace_id": evt.trace_id})


def to_messages(evt: Event, rt: "Runtime | None" = None) -> list[dict]:
    ts = evt.ts.isoformat()
    out: list[tuple[str, str, Any]] = []
    p = evt.payload
    if evt.topic in (Topic.VISION, Topic.MES):
        out.append(("events", evt.type, event_dict(evt)))
        if evt.type == "jev_vision":
            out.append(("ai", "jev_vision", {**p, "camera_id": evt.camera_id, "machine_id": evt.machine_id}))
    elif evt.topic == Topic.ALERTS:
        out.append(("incidents", evt.type, event_dict(evt)))
        if evt.type == "incident_opened":
            out.append(("events", evt.type, event_dict(evt)))
    elif evt.topic == Topic.TASKS:
        if evt.type == "workorder_transition":
            type_ = "escalated" if p.get("to") == "ESCALATED" else "status_changed"
        else:
            type_ = WO_TYPE.get(evt.type, "updated")
        data: dict[str, Any] = {"id": p.get("workorder_id"), "event": evt.type, **p}
        if rt is not None and p.get("workorder_id"):
            from .serialize import workorder_out

            wo = rt.db.get(Workorder, p["workorder_id"])
            if wo:
                data["workorder"] = workorder_out(rt, wo)
        out.append(("workorders", type_, data))
    elif evt.topic == Topic.AI:
        wid = p.get("workorder_id")
        if evt.type == "agent_message":
            out.append((f"agent:{wid}", "message", p["message"]))
        elif evt.type == "agent_reply":
            msg = p["message"]
            out.append((f"agent:{wid}", "delta", {"message_id": msg["id"], "text": msg["content"]}))
            out.append((f"agent:{wid}", "message", msg))
            if p.get("flags"):
                out.append((f"agent:{wid}", "safety_gate", {"message_id": msg["id"], "flags": p["flags"]}))
        elif evt.type == "safety_ack":
            out.append((f"agent:{wid}", "safety_gate", {"message_id": p["message_id"], "flags": p["flags"]}))
        else:
            type_ = {"diagnosis": "jev_result"}.get(evt.type, evt.type)
            out.append(("ai", type_, {**p, "trace_id": evt.trace_id}))
    return [{"channel": c, "type": t, "data": jsonable(d), "ts": ts} for c, t, d in out]


def wanted(channel: str, subs: set[str] | None) -> bool:
    if subs is None:
        return True
    return channel in subs or (channel.startswith("agent:") and "agent:*" in subs)


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    rt = websocket.app.state.rt
    await websocket.accept()
    name = f"ws-{new_id()[-6:]}"
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=2000)
    state: dict[str, Any] = {"subs": None, "role": websocket.query_params.get("role"),
                             "person_id": websocket.query_params.get("person_id")}
    queues = [rt.bus.subscribe(t, name, maxsize=500, lossy=True) for t in TOPICS]

    async def pump(q: asyncio.Queue[Event]) -> None:
        while True:
            evt = await q.get()
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(evt)

    async def sender() -> None:
        while True:
            evt = await queue.get()
            for msg in to_messages(evt, rt):
                if wanted(msg["channel"], state["subs"]):
                    await websocket.send_text(json.dumps(msg, ensure_ascii=False))

    async def metrics_loop() -> None:
        while True:
            await asyncio.sleep(5)
            if wanted("metrics", state["subs"]):
                await websocket.send_text(json.dumps({"channel": "metrics", "type": "summary",
                                                      "ts": rt.clock.now().isoformat(),
                                                      "data": jsonable(metrics.summary(rt.db))}, ensure_ascii=False))

    tasks = [asyncio.create_task(pump(q)) for q in queues] + [asyncio.create_task(sender()),
                                                               asyncio.create_task(metrics_loop())]
    try:
        await websocket.send_text(json.dumps({"channel": "system", "type": "hello", "ts": rt.clock.now().isoformat(),
                                              "data": {"channels": ["events", "incidents", "workorders", "ai",
                                                                    "agent:{workorder_id}", "metrics"],
                                                       "role": state["role"]}}))
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            action, channels = msg.get("action"), msg.get("channels")
            if action in ("subscribe", "unsubscribe") and isinstance(channels, list):
                subs = set() if state["subs"] is None else set(state["subs"])
                subs = subs | set(channels) if action == "subscribe" else subs - set(channels)
                state["subs"] = subs
            elif isinstance(msg.get("subscribe"), list):        # 舊格式
                state["subs"] = set(msg["subscribe"])
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        for q in queues:
            rt.bus.unsubscribe(q)
