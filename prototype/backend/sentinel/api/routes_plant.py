"""工廠、攝影機（MJPEG）、機台訊號。"""
from __future__ import annotations

import asyncio

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from sqlmodel import select

from ..db.models import Incident
from ..schemas.api import CameraSourceReq
from ..schemas.events import Event, Topic
from .deps import get_rt

router = APIRouter()


@router.get("/plant")
def plant(request: Request) -> dict:
    """plant.yaml ＋ 即時狀態：camera.source、machine.state／health／utilization（近 10 分鐘運轉比例）。"""
    rt = get_rt(request)
    data = rt.plant.model_dump()
    open_machines = {i.machine_id for i in rt.db.all(select(Incident).where(Incident.status == "open"))}
    for line in data["lines"]:
        for m in line["machines"]:
            states = rt.windows.mes(m["id"], "state").values(600)
            state = states[-1] if states else None
            m["state"] = state
            m["utilization"] = round(sum(1 for v in states if v == "running") / len(states), 3) if states else None
            m["health"] = "alarm" if state == "alarm" else ("warn" if m["id"] in open_machines else
                                                             ("ok" if state else "offline"))
        for c in line["cameras"]:
            cam = rt.cameras.get(c["id"])
            if cam:
                c["source"] = cam.video
                c["online"] = cam.online
                c["synthetic"] = cam.synthetic
    return data


@router.get("/cameras")
def cameras(request: Request) -> list[dict]:
    rt = get_rt(request)
    return [cam.describe() | {"overlay": rt.vision.overlays.get(cid, {})} for cid, cam in rt.cameras.items()]


def draw_overlay(frame: np.ndarray, cam_cfg, overlay: dict) -> np.ndarray:
    img = frame.copy()
    for name, (x1, y1, x2, y2) in cam_cfg.rois.items():
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 200, 0), 1)
        cv2.putText(img, name, (x1 + 4, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1)
    for v in overlay.get("ppe", []):
        x1, y1, x2, y2 = v["box"]
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 3)
        cv2.putText(img, "NO HELMET", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    jev = overlay.get("jev") or {}
    if jev.get("box"):
        x1, y1, x2, y2 = jev["box"]
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 140, 255), 3)
    if "p_jam" in jev:
        cv2.putText(img, f"Jev p_jam={jev['p_jam']:.2f}", (24, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 2)
    m = overlay.get("motion")
    if m:
        cv2.putText(img, f"motion {m['state']} {m['score']:.2f}", (24, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if m["state"] == "running" else (0, 200, 255), 2)
    return img


@router.get("/cameras/{camera_id}/stream")
async def stream(camera_id: str, request: Request, fps: float = 10) -> StreamingResponse:
    rt = get_rt(request)
    cam = rt.cameras.get(camera_id)
    if cam is None:
        raise HTTPException(404, f"找不到攝影機 {camera_id}")

    async def gen():
        while True:
            if await request.is_disconnected():
                break
            frame = cam.latest_frame()
            if frame is not None:
                img = draw_overlay(frame, cam.cam, rt.vision.overlays.get(camera_id, {}))
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            await asyncio.sleep(1 / max(fps, 1))

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.post("/cameras/{camera_id}/source")
async def set_source(camera_id: str, body: CameraSourceReq, request: Request) -> dict:
    rt = get_rt(request)
    cam = rt.cameras.get(camera_id)
    if cam is None:
        raise HTTPException(404, f"找不到攝影機 {camera_id}")
    cam.set_source(body.video, synthetic=body.synthetic, loop=body.loop)
    rt.audit.record("camera_source_changed", camera_id=camera_id, video=body.video, synthetic=body.synthetic)
    await rt.bus.publish(Event(ts=rt.clock.now(), topic=Topic.VISION, source="sim.camera", line=cam.cam.line,
                               machine_id=cam.cam.machine_id, camera_id=camera_id, type="camera_source",
                               payload={"video": body.video, "synthetic": cam.synthetic}))
    return {"ok": True, **cam.describe()}


@router.get("/machines/{machine_id}/signals")
def signals(machine_id: str, request: Request, last_min: float = 10) -> dict:
    """{signals: {名稱: [{ts, v}]}}；只回數值訊號（state、alarm_code 等文字訊號在 timeline）。"""
    rt = get_rt(request)
    if machine_id not in rt.plant.machines:
        raise HTTPException(404, f"找不到機台 {machine_id}")
    series = rt.windows.series(machine_id, last_min)
    out = {k: [{"ts": ts, "v": v} for ts, v in pts] for k, pts in series.items()
           if pts and all(isinstance(v, (int, float)) and not isinstance(v, bool) for _, v in pts)}
    return {"machine_id": machine_id, "last_min": last_min, "signals": out,
            "timeline": list(rt.windows.timeline.get(machine_id, []))}
