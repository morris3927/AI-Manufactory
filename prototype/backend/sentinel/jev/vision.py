"""V1／V2：關鍵幀視覺判斷。

圖片規格（Jev整合規劃.md 3.1）：PNG／JPEG；每邊 256–1,568 像素；總像素 78,400–2,458,624。
程式用 288 當下限（留餘裕）；Jev 回 400 時 client 會重試一次。
"""
from __future__ import annotations

import base64
import math

import cv2
import numpy as np
from pydantic import BaseModel

from . import questions as Q
from .client import JevClient

MIN_SIDE, MAX_SIDE = 288, 1568          # 規格下限 256；實測貼近下限偶爾回 400，留 32 px 餘裕
MIN_PIXELS, MAX_PIXELS = 78_400, 2_458_624


def fit_size(w: int, h: int, max_side: int = 768) -> tuple[int, int, int, int]:
    """回傳 (縮放後寬, 高, 補邊後寬, 高)。先依 max_side 縮小，再保證下限；極端長寬比用補邊。"""
    max_side = min(max_side, MAX_SIDE)
    s = min(max_side / max(w, h), 1.0)
    if min(w, h) * s < MIN_SIDE:
        s = MIN_SIDE / min(w, h)
    if w * h * s * s < MIN_PIXELS:
        s = math.sqrt(MIN_PIXELS / (w * h)) * 1.001
    if max(w, h) * s > MAX_SIDE:            # 長寬比太極端：長邊壓到上限，短邊補黑邊
        s = MAX_SIDE / max(w, h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    pw, ph = max(nw, MIN_SIDE), max(nh, MIN_SIDE)
    while pw * ph < MIN_PIXELS:
        if pw <= ph:
            pw += 1
        else:
            ph += 1
    return nw, nh, pw, ph


def prepare_image(frame_bgr: np.ndarray, roi: list[int] | None = None, max_side: int = 768) -> np.ndarray:
    img = frame_bgr
    if roi:
        x1, y1, x2, y2 = roi
        img = frame_bgr[y1:y2, x1:x2]
    h, w = img.shape[:2]
    nw, nh, pw, ph = fit_size(w, h, max_side)
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if nw < w else cv2.INTER_LINEAR)
    if (pw, ph) != (nw, nh):
        canvas = np.zeros((ph, pw, 3), np.uint8)
        canvas[:nh, :nw] = img
        img = canvas
    return img


def to_part(frame_bgr: np.ndarray, roi: list[int] | None = None, max_side: int = 768, quality: int = 85) -> dict:
    """裁 ROI → 縮放到 Jev 允許的範圍 → JPEG base64 image part。"""
    img = prepare_image(frame_bgr, roi, max_side)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG 編碼失敗")
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}}


class VisionJudgement(BaseModel):
    p_jam: float
    p_smoke: float
    box: list[int] | None = None          # 全幀像素座標 x1, y1, x2, y2；只有 p_jam 夠高時才採用
    box_conf: float | None = None
    model: str | None = None
    cost: float | None = None
    latency_ms: float | None = None


class PpeJudgement(BaseModel):
    p_no_helmet: float
    p_in_zone: float
    model: str | None = None
    cost: float | None = None


async def feed(jev: JevClient, frame: np.ndarray, roi: list[int] | None, machine: str, *,
               p_jam_box: float = 0.7, trace_id: str | None = None, max_side: int = 768,
               quality: int = 85) -> VisionJudgement:
    state = {"machine": machine, "camera": "送料口", "roi_image": to_part(frame, roi, max_side, quality)}
    res = await jev.ask(state, Q.FEED_QUESTIONS, purpose="vision.feed", trace_id=trace_id, questions_ver=Q.FEED_VER)
    p_jam = res.noul("jammed")
    box_px = None
    b = res.box("jam_box")
    if b is not None and p_jam > p_jam_box:      # Box 沒有「找不到」，只有 p_jam 夠高時才採用
        x0, y0 = (roi[0], roi[1]) if roi else (0, 0)
        rw = (roi[2] - roi[0]) if roi else frame.shape[1]
        rh = (roi[3] - roi[1]) if roi else frame.shape[0]
        box_px = [round(x0 + b.box.left * rw), round(y0 + b.box.top * rh),
                  round(x0 + b.box.right * rw), round(y0 + b.box.bottom * rh)]
    return VisionJudgement(p_jam=p_jam, p_smoke=res.noul("smoke"), box=box_px,
                           box_conf=b.confidence if b else None, model=res.model, cost=res.cost,
                           latency_ms=res.latency_ms)


async def ppe(jev: JevClient, person_crop_blurred: np.ndarray, *, trace_id: str | None = None) -> PpeJudgement:
    """V1：人員裁切圖必須先經過 face_blur。"""
    state = {"area": "組裝線作業區", "person_image": to_part(person_crop_blurred)}
    res = await jev.ask(state, Q.PPE_QUESTIONS, purpose="vision.ppe", trace_id=trace_id, questions_ver=Q.PPE_VER)
    return PpeJudgement(p_no_helmet=res.noul("no_helmet"), p_in_zone=res.noul("in_work_zone"),
                        model=res.model, cost=res.cost)


class SmokeJudgement(BaseModel):
    p_smoke_outside: float
    p_flame: float
    value: str                            # none／outside_cell（signals.yaml vision.smoke）
    box: list[int] | None = None
    model: str | None = None
    cost: float | None = None
    latency_ms: float | None = None


async def smoke(jev: JevClient, frame: np.ndarray, cell_roi: list[int] | None, machine: str, *, p_thr: float = 0.7,
                trace_id: str | None = None, max_side: int = 768, quality: int = 85) -> SmokeJudgement:
    """銲接工作站：工位 ROI 內的火花與銲煙是正常的，只判斷 ROI 以外。送整幀並畫出黃色工位框。"""
    img = frame.copy()
    if cell_roi:
        x1, y1, x2, y2 = cell_roi
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 220, 255), 4)
    state = {"machine": machine, "camera": "銲接工作站", "cell_image": to_part(img, None, max_side, quality)}
    res = await jev.ask(state, Q.SMOKE_QUESTIONS, purpose="vision.smoke", trace_id=trace_id,
                        questions_ver=Q.SMOKE_VER)
    p_s, p_f = res.noul("smoke_outside_cell"), res.noul("flame_outside_cell")
    b = res.box("smoke_box")
    box = None
    if b is not None and p_s > p_thr:
        h, w = frame.shape[:2]
        box = [round(b.box.left * w), round(b.box.top * h), round(b.box.right * w), round(b.box.bottom * h)]
    return SmokeJudgement(p_smoke_outside=p_s, p_flame=p_f, value="outside_cell" if p_s > p_thr else "none", box=box,
                          model=res.model, cost=res.cost, latency_ms=res.latency_ms)
