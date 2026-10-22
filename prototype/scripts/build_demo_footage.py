"""用公開授權的真實照片合成 Demo 用「監視畫面」影片（1280×720、15 fps）。

為什麼：團隊還沒有自己拍攝的產線影片；純程式畫的方塊畫面讓 YOLO／Jev 看不到任何真實內容。
這支程式把公開授權照片做成固定機位的監視畫面（緩慢推拉、感光雜訊、壓縮、時間戳記），
並在需要「異常」的片段做影像編輯（卡料板材、煙霧、機器人停止）。所有輸出都在畫面上標示
「模擬監視畫面（公開授權照片合成）」；有影像編輯的攝影機另外標示「合成」。

來源（逐檔記錄於 assets/videos/SOURCES.csv）：
- Pexels 照片（Pexels License）。挑選依據 SH17 資料集（CC BY-NC-SA 4.0）的標註與本專案 YOLO 實測；
  SH17 只用來挑圖，影片裡的像素直接取自 Pexels 原圖。
- Wikimedia Commons（CC BY / CC BY-SA，建置時再向 Commons API 確認授權，不符就中止）。

照片下載到 ~/.cache/sentinel-footage（不進 repo）；影片輸出到 assets/videos/**（*.mp4 已被 .gitignore）。
劇本時間（卡料／冒煙／脫帽發生秒數）直接讀 data/simulated/scenarios/*.yaml，改劇本後重跑即可。

用法（prototype/ 底下）：
    backend/.venv/bin/python scripts/build_demo_footage.py            # 下載來源＋產生全部影片＋SOURCES.csv
    backend/.venv/bin/python scripts/build_demo_footage.py --only ppe_violation_01 --preview 3,6
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

PROTO = Path(__file__).resolve().parents[1]
VIDEOS = PROTO / "assets" / "videos"
SCENARIOS = PROTO / "data" / "simulated" / "scenarios"
CACHE = Path(os.environ.get("SENTINEL_FOOTAGE_CACHE", Path.home() / ".cache" / "sentinel-footage"))
W, H, FPS = 1280, 720, 15
UA = "SentinelDemoFootage/0.1 (student competition; low volume)"
BANNER = "模擬監視畫面（公開授權照片合成）"
OK_LICENSES = {"CC BY 3.0", "CC BY 4.0", "CC BY-SA 3.0", "CC BY-SA 4.0", "CC0", "Public domain"}

# ── 來源 ────────────────────────────────────────────────────────
PEXELS_LICENSE = "Pexels License（免費使用、可改作；挑選依據 SH17 標註，SH17 為 CC BY-NC-SA 4.0）"


@dataclass
class Source:
    key: str
    kind: str                         # pexels／commons
    ref: str                          # Pexels 照片 ID 或 Commons 檔名
    author: str = ""
    page: str = ""
    license: str = ""
    note: str = ""
    focus: tuple[float, float] = (0.5, 0.5)     # 裁成 16:9 時的取景中心（0–1）

    @property
    def path(self) -> Path:
        return CACHE / f"{self.key}.jpg"


SOURCES: dict[str, Source] = {s.key: s for s in [
    # 組裝線作業區（CAM-ASM-01）：Pexels 連號的金屬加工廠系列照片
    Source("pexels_16045333", "pexels", "16045333", note="兩名作業員戴安全帽，在配電盤前"),
    Source("pexels_15947456", "pexels", "15947456", note="三名作業員戴安全帽"),
    Source("pexels_15947457", "pexels", "15947457", note="作業員戴安全帽攀爬設備"),
    Source("pexels_16045331", "pexels", "16045331", note="一人戴安全帽、一人未戴（違規畫面）"),
    Source("pexels_16045335", "pexels", "16045335", note="雷射切割機旁作業員未戴安全帽"),
    Source("pexels_15016528", "pexels", "15016528", note="木材加工機台旁作業員戴安全帽"),
    # 組裝站近景（CAM-ASM-02，不跑偵測器）
    Source("pexels_15016523", "pexels", "15016523", note="組裝包裝區"),
    # 沖壓送料口（CAM-STP-01）
    Source("commons_air_feeder", "commons", "File:Air Feeder.jpg", note="銅帶經氣動送料機進入沖床模具"),
    # 銲接（CAM-WLD-01／02）
    Source("commons_welding_robot", "commons", "File:Welding Robot.jpg", note="銲接機器人工作站，工位內火花"),
    Source("commons_fanuc_reaching", "commons", "File:FANUC welding robot reaching.jpg", note="六軸銲接機器人"),
]}

# SH17 meta-data（Pexels 原頁與攝影師）；Kaggle 限流時用這份快取
PEXELS_META_FILE = PROTO / "assets" / "videos" / "pexels_meta.json"


def _curl(url: str, out: Path | None = None) -> bytes:
    args = ["curl", "-sfL", "--retry", "2", "-A", UA, url]
    if out:
        args += ["-o", str(out)]
    return subprocess.run(args, capture_output=True, check=True).stdout


def fetch_sources(keys: set[str]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    meta_path = CACHE / "commons_meta.json"
    cmeta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    pmeta = json.loads(PEXELS_META_FILE.read_text()) if PEXELS_META_FILE.exists() else {}
    for k in sorted(keys):
        s = SOURCES[k]
        if s.kind == "commons":
            if s.ref not in cmeta:
                q = urllib.parse.urlencode({"action": "query", "format": "json", "titles": s.ref, "prop": "imageinfo",
                                            "iiprop": "url|size|extmetadata", "iiurlwidth": 1920,
                                            "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl"})
                d = json.loads(_curl("https://commons.wikimedia.org/w/api.php?" + q))
                ii = next(iter(d["query"]["pages"].values()))["imageinfo"][0]
                m = ii["extmetadata"]
                g = lambda f: re.sub(r"\s+", " ", re.sub("<[^>]+>", "", m.get(f, {}).get("value", ""))).strip()  # noqa: E731
                cmeta[s.ref] = {"page": ii["descriptionurl"], "license": g("LicenseShortName"),
                                "license_url": g("LicenseUrl"), "author": g("Artist")[:120],
                                "url": ii.get("thumburl") if ii["width"] > 1920 else ii["url"]}
                meta_path.write_text(json.dumps(cmeta, ensure_ascii=False, indent=1))
                time.sleep(1)
            m = cmeta[s.ref]
            if m["license"] not in OK_LICENSES:
                sys.exit(f"{s.ref} 授權為 {m['license']!r}，不在允許清單，停止")
            s.page, s.license, s.author = m["page"], m["license"], m["author"]
            url = m["url"]
        else:
            m = pmeta.get(s.ref, {})
            s.page = m.get("url") or f"https://www.pexels.com/photo/{s.ref}/"
            s.author = m.get("photographer", "")
            s.license = PEXELS_LICENSE
            url = f"https://images.pexels.com/photos/{s.ref}/pexels-photo-{s.ref}.jpeg?auto=compress&w=1920"
        if not s.path.exists():
            print(f"下載 {s.key} ← {url[:90]}")
            _curl(url, s.path)
            time.sleep(0.5)


# ── 影像工具 ────────────────────────────────────────────────────
def fit16x9(img: np.ndarray, focus=(0.5, 0.5), out_w: int = 1920) -> np.ndarray:
    """裁成 16:9 後縮放到工作解析度（out_w×out_w*9/16）。"""
    h, w = img.shape[:2]
    tw, th = w, round(w * 9 / 16)
    if th > h:
        th, tw = h, round(h * 16 / 9)
    x = int(np.clip(focus[0] * w - tw / 2, 0, w - tw))
    y = int(np.clip(focus[1] * h - th / 2, 0, h - th))
    out_h = round(out_w * 9 / 16)
    crop = img[y:y + th, x:x + tw]
    return cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_AREA if tw > out_w else cv2.INTER_CUBIC)


def load(key: str) -> np.ndarray:
    s = SOURCES[key]
    img = cv2.imread(str(s.path))
    if img is None:
        raise FileNotFoundError(s.path)
    return fit16x9(img, s.focus)


def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def view(work: np.ndarray, zoom: float, cx: float, cy: float, dx: float = 0, dy: float = 0) -> np.ndarray:
    """從工作影像取 1280×720 視窗：zoom=1 為全幅；(cx, cy) 為視窗中心（0–1）；dx/dy 為輸出像素位移（震動）。"""
    hw, ww = work.shape[:2]
    s = (ww / zoom) / W                              # 輸出 1 px 對應來源 s px
    half_w, half_h = ww / zoom / 2, hw / zoom / 2
    cxp = np.clip(cx * ww, half_w, ww - half_w)
    cyp = np.clip(cy * hw, half_h, hw - half_h)
    M = np.float32([[s, 0, cxp - half_w - dx * s], [0, s, cyp - half_h - dy * s]])
    return cv2.warpAffine(work, M, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)


@dataclass
class Shot:
    key: str
    t0: float
    t1: float
    z: tuple[float, float] = (1.0, 1.06)             # 起訖縮放
    c0: tuple[float, float] = (0.5, 0.5)
    c1: tuple[float, float] = (0.5, 0.5)

    def render(self, work: np.ndarray, t: float) -> np.ndarray:
        u = ease((t - self.t0) / max(self.t1 - self.t0, 1e-6))
        z = self.z[0] + (self.z[1] - self.z[0]) * u
        cx = self.c0[0] + (self.c1[0] - self.c0[0]) * u
        cy = self.c0[1] + (self.c1[1] - self.c0[1]) * u
        return view(work, z, cx, cy)


def sequence(shots: list[Shot], fade: float = 1.0) -> Callable[[float], np.ndarray]:
    """依時間交叉淡化多張照片（模擬時間經過）。shot 的 t1 之後再延續 fade 秒做淡出。"""
    works = {s.key: load(s.key) for s in shots}

    def at(t: float) -> np.ndarray:
        active = [s for s in shots if s.t0 - 1e-9 <= t < s.t1 + fade]
        if not active:
            active = [shots[-1]]
        cur = active[-1]
        img = cur.render(works[cur.key], t).astype(np.float32)
        if len(active) > 1 and t < cur.t0 + fade:            # 與前一張交叉淡化
            prev = active[-2]
            a = ease((t - cur.t0) / fade)
            img = prev.render(works[prev.key], t).astype(np.float32) * (1 - a) + img * a
        return img.clip(0, 255).astype(np.uint8)
    return at


# ── 監視器質感與標示 ────────────────────────────────────────────
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/msjh.ttc", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FONT_S, FONT_M = _font(18), _font(22)


class Look:
    """感光雜訊、輕微失焦、暗角、JPEG 壓縮（固定亂數種子，可重現）。"""

    def __init__(self, seed: int, noise: float = 3.0, sat: float = 0.85, jpeg: int = 62):
        self.rng = np.random.default_rng(seed)
        self.noise, self.sat, self.jpeg = noise, sat, jpeg
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        self.vignette = (1 - 0.18 * np.clip(r - 0.35, 0, 1) ** 1.5)[..., None]

    def __call__(self, img: np.ndarray) -> np.ndarray:
        f = cv2.GaussianBlur(img, (3, 3), 0.6).astype(np.float32)
        gray = f.mean(axis=2, keepdims=True)
        f = gray + (f - gray) * self.sat
        f = f * self.vignette + self.rng.normal(0, self.noise, f.shape).astype(np.float32)
        out = f.clip(0, 255).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)


T_BASE = 9 * 3600          # 時間戳記從 09:00:00 起算（config.yaml app.sim_start 為日班 09:00）


def overlay(img: np.ndarray, cam_id: str, t: float, tag: str | None) -> np.ndarray:
    """左上：攝影機編號＋時間；下方：合成標示（固定在畫面最下方 32 px，避開 ROI）。"""
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil, "RGBA")
    s = T_BASE + int(t)
    ts = f"2026-10-30 {s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    d.rectangle((0, 0, 330, 34), fill=(0, 0, 0, 140))
    d.text((10, 6), f"{cam_id}  {ts}", font=FONT_M, fill=(255, 255, 255, 235))
    text = BANNER + (f"　{tag}" if tag else "")
    d.rectangle((0, H - 32, W, H), fill=(0, 0, 0, 150))
    d.text((10, H - 28), text, font=FONT_S, fill=(255, 220, 120, 240))
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)


# ── 沖壓：送料口（Air Feeder.jpg）───────────────────────────────
def strip_mask(work: np.ndarray) -> np.ndarray:
    """銅帶（橘色、中高飽和），只取進料段（右半部）。"""
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (5, 60, 120), (22, 200, 255))
    h, w = m.shape
    m[:, : int(w * 0.45)] = 0
    m[: int(h * 0.30)] = 0
    m[int(h * 0.78):] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    return m


class FeedCam:
    """固定機位。運轉中：沖床每分鐘 42 衝程造成的機身震動＋銅帶逐步送入；停止：只剩感光雜訊。
    卡料（合成）：在送料口疊上一片翹起的銅帶與一片錯位疊料（取自照片中銅帶的像素做透視變形，影像編輯，非真實卡料）。"""

    # 銅帶在輸出畫面（1280×720）的位置：左端為進模點（量測自 strip_mask）
    STRIP_SEG = np.float32([[640, 263], [1000, 353], [1000, 446], [640, 330]])    # 取樣段：上左、上右、下右、下左

    def __init__(self):
        self.work = load("commons_air_feeder")
        self.k = self.work.shape[1] / W                       # 工作影像／輸出 的比例
        self.mask = strip_mask(self.work)
        self.spm = 42
        self._jam_cache: dict[int, np.ndarray] = {}

    def _sheet(self, img: np.ndarray, dst: np.ndarray, shade: float, shadow_off: tuple[float, float]) -> np.ndarray:
        """把銅帶取樣段透視貼到 dst 四邊形（輸出座標），附陰影。"""
        k = self.k
        src = self.STRIP_SEG * k
        dst = dst.astype(np.float32) * k
        h, w = img.shape[:2]
        P = cv2.getPerspectiveTransform(src, dst)
        tex = cv2.warpPerspective(self.work, P, (w, h)).astype(np.float32)
        m = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(m, dst.astype(np.int32), 255)
        sh = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(sh, (dst + np.float32(shadow_off) * k).astype(np.int32), 255)
        sh_a = (cv2.GaussianBlur(sh, (0, 0), 9).astype(np.float32) / 255 * 0.55)[..., None]
        out = img.astype(np.float32) * (1 - sh_a)
        # 沿長邊的明暗（彎曲反光）
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = dst
        ax = (xx - d[0, 0]) * (d[1, 0] - d[0, 0]) + (yy - d[0, 1]) * (d[1, 1] - d[0, 1])
        ax /= max(float(np.sum((d[1] - d[0]) ** 2)), 1.0)
        gloss = 0.78 + 0.32 * np.exp(-((ax - 0.35) / 0.18) ** 2)
        a = (cv2.GaussianBlur(m, (5, 5), 0).astype(np.float32) / 255)[..., None]
        return (out * (1 - a) + tex * (shade * gloss)[..., None] * a).clip(0, 255)

    def jam_img(self, u: float) -> np.ndarray:
        """u：0 = 正常、1 = 完全卡住（上層銅帶在進模點翹起約 32°＋錯位疊料）。"""
        if u <= 0:
            return self.work
        key = int(u * 40)
        if key in self._jam_cache:
            return self._jam_cache[key]
        u = key / 40
        seg = self.STRIP_SEG
        piv = (seg[0] + seg[3]) / 2                              # 進模點
        L = 360.0
        ang = math.radians(32 * u)
        base_dir = (seg[1] - seg[0]) / np.linalg.norm(seg[1] - seg[0])
        rot = np.float32([[math.cos(-ang), -math.sin(-ang)], [math.sin(-ang), math.cos(-ang)]])
        d = rot @ base_dir * L * (1 - 0.12 * u)                   # 透視縮短
        half = (seg[3] - seg[0]) / 2
        lifted = np.float32([piv - half, piv - half + d, piv + half * 1.05 + d, piv + half])
        img = self.work.astype(np.float32)
        if u > 0.35:                                              # 錯位疊料（第二片擠上來壓在入口）
            v = min((u - 0.35) / 0.65, 1.0)
            off = np.float32([-55, -42]) * v
            stacked = seg + off
            stacked[1] += np.float32([0, -25]) * v
            stacked[2] += np.float32([0, -25]) * v
            img = self._sheet(img, stacked, 0.92, (10, 14))
        img = self._sheet(img, lifted, 0.86, (14, 26 * u))
        out = img.clip(0, 255).astype(np.uint8)
        self._jam_cache[key] = out
        return out

    def frame(self, t: float, *, running: bool, jam_u: float = 0.0) -> np.ndarray:
        dx = dy = 0.0
        img = self.jam_img(jam_u) if jam_u > 0 else self.work
        if running:
            ph = (t * self.spm / 60) % 1.0                       # 衝程相位
            jolt = math.exp(-ph * 9) * 3.0                       # 下死點衝擊（3 px，快速衰減）
            dy = jolt * math.sin(ph * 40) + 0.8 * math.sin(t * 23.0)
            dx = 0.6 * math.sin(t * 31.0)
            if jam_u <= 0:                                       # 銅帶送進：沿料帶方向的鋸齒位移
                feed = (ph * 14.0)
                M = np.float32([[1, 0, -feed * 0.93], [0, 1, -feed * 0.37]])
                moved = cv2.warpAffine(self.work, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)
                a = (cv2.GaussianBlur(self.mask, (9, 9), 0).astype(np.float32) / 255)[..., None]
                img = (img * (1 - a) + moved * a).astype(np.uint8)
        return view(img, 1.0 + 0.01 * math.sin(t / 9), 0.5, 0.5, dx, dy)


# ── 銲接：機器人工作站（Welding Robot.jpg）──────────────────────
class WeldCam:
    """工位內火花：從原照片分離火花圖層後逐幀閃爍、抖動；機器人停止 → 火花熄滅（影像編輯）。
    工位外煙霧（合成）：程式產生的多層雜訊煙霧，從工位左後方冒出並上升擴散。"""

    def __init__(self, seed: int = 7):
        self.work = load("commons_welding_robot")
        hsv = cv2.cvtColor(self.work, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        m = cv2.inRange(hsv, (5, 70, 170), (32, 255, 255))
        region = np.zeros_like(m)
        region[int(h * 0.42):int(h * 0.97), int(w * 0.52):w] = 255                    # 火花只在電弧附近
        m = cv2.bitwise_and(m, region)
        bright = cv2.inRange(hsv, (0, 0, 235), (40, 120, 255))                        # 電弧中心的白熱區
        m = cv2.bitwise_or(m, cv2.bitwise_and(bright, region))
        self.spark_mask = cv2.dilate(m, np.ones((5, 5), np.uint8))
        # 火花移除後的背景：低解析度修補再放大（大面積修補在原解析度會拉出橘色條紋），並降低飽和度
        q = 4
        small = cv2.resize(self.work, (w // q, h // q), interpolation=cv2.INTER_AREA)
        sm = cv2.dilate(cv2.resize(self.spark_mask, (w // q, h // q), interpolation=cv2.INTER_AREA), np.ones((3, 3), np.uint8))
        fill = cv2.resize(cv2.inpaint(small, (sm > 0).astype(np.uint8) * 255, 5, cv2.INPAINT_NS), (w, h),
                          interpolation=cv2.INTER_CUBIC)
        fill = cv2.GaussianBlur(fill, (0, 0), 6)
        g = cv2.cvtColor(cv2.cvtColor(fill, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        fill = (0.35 * fill + 0.65 * g) * 0.8
        a = (cv2.GaussianBlur(self.spark_mask, (0, 0), 3).astype(np.float32) / 255)[..., None]
        self.base = (self.work * (1 - a) + fill * a).clip(0, 255).astype(np.uint8)
        self.sparks = cv2.subtract(self.work, self.base).astype(np.float32)          # 火花圖層（加光）
        self.arc = (w * 0.63, h * 0.62)
        self.rng = np.random.default_rng(seed)
        self.noise = [self._noise(512, sc, seed + i) for i, sc in enumerate((8, 16, 32))]

    @staticmethod
    def _noise(n: int, cells: int, seed: int) -> np.ndarray:
        r = np.random.default_rng(seed).random((cells, cells)).astype(np.float32)
        r = np.tile(r, (2, 2))
        return cv2.resize(r, (2 * n, 2 * n), interpolation=cv2.INTER_CUBIC)

    def spark_layer(self, t: float) -> np.ndarray:
        k = int(t * FPS)
        rng = np.random.default_rng(1000 + k)
        ang = rng.uniform(-6, 6)
        sc = rng.uniform(0.85, 1.12)
        M = cv2.getRotationMatrix2D(self.arc, ang, sc)
        layer = cv2.warpAffine(self.sparks, M, (self.work.shape[1], self.work.shape[0]))
        return layer * rng.uniform(0.55, 1.25)

    def smoke(self, t: float, age: float, roi_out: tuple[int, int, int, int]) -> tuple[np.ndarray, np.ndarray]:
        """回傳 (alpha HxW, 顏色 HxWx3)，座標為輸出 1280×720。煙柱從 roi_out 底部中央冒出，往上擴散並微微右飄。"""
        x1, y1, x2, y2 = roi_out
        grow = min(age / 3.0, 1.0)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        src_x, src_y = (x1 + x2) / 2, float(y2)
        top = src_y - (200 + 520 * grow)                          # 煙柱目前升到的高度
        hgt = np.clip((src_y - yy) / max(src_y - top, 1), 0, None)   # 0（底）→ 1（頂）
        width = 70 + 260 * hgt ** 0.8                             # 越高越寬
        cx = src_x + 60 * hgt ** 1.5 + 18 * np.sin(yy / 60 + t * 0.8)
        plume = np.exp(-((xx - cx) / width) ** 2) * (hgt <= 1.15) * np.exp(-np.clip(hgt - 0.85, 0, None) * 5)
        plume *= np.clip((src_y + 15 - yy) / 30, 0, 1)
        tex = np.zeros((H, W), np.float32)
        for i, nz in enumerate(self.noise):                       # 往上捲動的多層雜訊（翻滾感）
            off = int((t * (45 + 35 * i)) % 512)
            tex += cv2.resize(nz[off:off + 512, (17 * i) % 256:(17 * i) % 256 + 512], (W, H)) * (0.55 ** i)
        tex = (tex / 1.85 - 0.15).clip(0, 1)
        alpha = np.clip(plume * (0.75 + 1.3 * tex) * (0.65 + 0.45 * grow), 0, 0.96)
        col = np.zeros((H, W, 3), np.float32)
        shade = 38 + 60 * tex * (0.5 + 0.5 * hgt.clip(0, 1))      # 底部深、上方較淡
        col[..., 0] = shade
        col[..., 1] = shade + 3
        col[..., 2] = shade + 9
        return cv2.GaussianBlur(alpha, (0, 0), 5), col

    def frame(self, t: float, *, sparks: float, smoke_age: float | None = None,
              smoke_roi: tuple[int, int, int, int] | None = None) -> np.ndarray:
        img = self.base.astype(np.float32)
        if sparks > 0:
            img = img + self.spark_layer(t) * sparks
        out = view(img.clip(0, 255).astype(np.uint8), 1.0 + 0.012 * math.sin(t / 8), 0.5, 0.5)
        if smoke_age is not None and smoke_age >= 0 and smoke_roi:
            a, col = self.smoke(t, smoke_age, smoke_roi)
            out = (out.astype(np.float32) * (1 - a[..., None]) + col * a[..., None]).clip(0, 255).astype(np.uint8)
        return out


# ── 影片定義 ────────────────────────────────────────────────────
def scen(name: str) -> dict:
    return yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))


def mes_time(sc: dict, machine: str, key: str, pred: Callable) -> float | None:
    for step in sc.get("mes_script", {}).get(machine, []):
        if pred(step.get("set", {}).get(key)):
            return float(step["t"])
    return None


# 依照 plant.yaml 的 ROI（輸出座標）
ROI_FEED = (260, 60, 1280, 680)              # CAM-STP-01 feed_inlet：銅帶從右邊進入模具
ROI_WELD = (470, 90, 1280, 680)              # CAM-WLD-01 weld_cell：機器人與電弧（火花在框內屬正常）
SMOKE_SRC = (40, 260, 400, 560)              # 工位外（左後方，安全圍籬外）煙霧來源區


@dataclass
class Clip:
    name: str                        # assets/videos/ 底下的相對路徑（不含 .mp4）
    camera: str
    duration: float
    render: Callable[[float], np.ndarray]
    sources: list[str]
    edits: str                       # 寫進 SOURCES.csv 的改作說明
    tag: Callable[[float], str | None] = lambda t: None
    seed: int = 1
    look: dict = field(default_factory=dict)


def build_clips() -> list[Clip]:
    clips: list[Clip] = []

    # 組裝線作業區（CAM-ASM-01）：正常＝全員戴安全帽
    normal = [Shot("pexels_16045333", 0, 9, (1.0, 1.08), (0.45, 0.5), (0.5, 0.45)),
              Shot("pexels_15947456", 10, 19, (1.0, 1.06), (0.5, 0.5), (0.47, 0.52)),
              Shot("pexels_15947457", 20, 29, (1.0, 1.07), (0.52, 0.48), (0.5, 0.5)),
              Shot("pexels_15016528", 30, 39, (1.0, 1.06), (0.5, 0.5), (0.53, 0.5))]
    clips.append(Clip("assembly/normal_01", "CAM-ASM-01", 40, sequence(normal), [s.key for s in normal],
                      "16:9 裁切、緩慢推拉、照片間交叉淡化、雜訊與壓縮、疊加時間與標示", seed=11))

    sc = scen("ppe_violation")
    t_v = float(sc["expect"]["t0"])                           # 未戴安全帽的人出現時間
    # 違規畫面先用 16045335（正面、頭部清楚，Jev V1 實測 p_no_helmet 0.65–0.73）；16045331（背影）Jev 判不出來，放後段
    ppe = [Shot("pexels_16045333", 0, t_v - 0.5, (1.0, 1.05), (0.45, 0.5), (0.48, 0.48)),
           Shot("pexels_16045335", t_v - 0.5, t_v + 9, (1.0, 1.05), (0.5, 0.5), (0.52, 0.48)),
           Shot("pexels_16045331", t_v + 9, sc.get("duration_s", 15) + 10, (1.0, 1.04), (0.5, 0.5), (0.48, 0.52))]
    clips.append(Clip("assembly/ppe_violation_01", "CAM-ASM-01", sc.get("duration_s", 15) + 10, sequence(ppe, fade=0.8),
                      [s.key for s in ppe],
                      f"Pexels 連號系列照片：{t_v:g} 秒前為戴安全帽畫面，{t_v - 0.5:g}–{t_v + 0.3:g} 秒交叉淡化到有人未戴安全帽的照片"
                      "（模擬時間經過，非連續拍攝）；緩慢推拉、雜訊與壓縮、疊加時間與標示", seed=12))

    # 組裝站近景（CAM-ASM-02，只看畫面）
    st = [Shot("pexels_15016523", 0, 40, (1.0, 1.08), (0.5, 0.5), (0.55, 0.5))]
    for n in ("station_01", "screw_nok_01"):
        clips.append(Clip(f"assembly/{n}", "CAM-ASM-02", 40, sequence(st), ["pexels_15016523"],
                          "16:9 裁切、緩慢推拉、雜訊與壓縮、疊加時間與標示（畫面本身無異常，鎖附 NOK 由 Log 判定）", seed=13))

    # 沖壓送料口（CAM-STP-01）
    feed = FeedCam()
    edit_feed = "固定機位微幅漂移；沖床衝程震動與銅帶送入為程式位移；雜訊與壓縮、疊加時間與標示"
    tag_feed = "合成：震動／送料動態為程式產生"
    clips.append(Clip("stamping/normal_01", "CAM-STP-01", 30, lambda t: feed.frame(t, running=True),
                      ["commons_air_feeder"], edit_feed, tag=lambda t: tag_feed, seed=21))
    clips.append(Clip("stamping/person_zone_01", "CAM-STP-01", 20, lambda t: feed.frame(t, running=True),
                      ["commons_air_feeder"], edit_feed + "（人員進入危險區事件由劇本注入，畫面中沒有人）",
                      tag=lambda t: tag_feed, seed=22))
    clips.append(Clip("stamping/idle_01", "CAM-STP-01", 40, lambda t: feed.frame(t, running=False),
                      ["commons_air_feeder"], "固定機位、無衝程震動（機台閒置）；雜訊與壓縮、疊加時間與標示",
                      tag=lambda t: tag_feed, seed=23))
    sc = scen("feed_jam")
    cue = sc["cameras"]["CAM-STP-01"]
    t_jam = float(cue.get("jam_at_s", 10))
    t_stop = mes_time(sc, "STP-L1-FD02", "feed_rate", lambda v: v == 0) or float(sc["expect"]["t0"])
    jam_ramp = 1.5

    def jam_frame(t: float) -> np.ndarray:
        return feed.frame(t, running=t < t_stop, jam_u=ease((t - t_jam) / jam_ramp) if t >= t_jam else 0.0)

    clips.append(Clip("stamping/feed_jam_01", "CAM-STP-01", sc.get("duration_s", 25) + 5, jam_frame, ["commons_air_feeder"],
                      f"**合成卡料**：{t_jam:g} 秒起在進模點疊上一片翹起約 32° 的銅帶與一片錯位疊料"
                      f"（取自照片中銅帶像素做透視變形與陰影，影像編輯，非真實卡料）；{t_stop:g} 秒後無衝程震動（送料與沖床停止）；其餘同 normal_01",
                      tag=lambda t: "合成：卡料板材為影像編輯" if t >= t_jam else tag_feed, seed=24))

    # 銲接機器人工作站（CAM-WLD-01）
    weld = WeldCam()
    tag_weld = "合成：火花閃爍／煙霧為程式產生"
    clips.append(Clip("welding/normal_01", "CAM-WLD-01", 30, lambda t: weld.frame(t, sparks=1.0),
                      ["commons_welding_robot"], "工位內火花自原照片分離後逐幀閃爍抖動（程式產生）；微幅漂移、雜訊與壓縮、疊加時間與標示",
                      tag=lambda t: tag_weld, seed=31))
    sc = scen("weld_smoke")
    t_smoke = float(sc["cameras"]["CAM-WLD-01"].get("event_at_s", 8))
    t_rstop = mes_time(sc, "WLD-L1-RC01", "robot_state", lambda v: v == "stopped") or max(0.0, t_smoke - 3)

    def smoke_frame(t: float) -> np.ndarray:
        sp = 1.0 if t < t_rstop else max(0.0, 1 - (t - t_rstop) / 0.4)
        return weld.frame(t, sparks=sp, smoke_age=(t - t_smoke) if t >= t_smoke else None, smoke_roi=SMOKE_SRC)

    clips.append(Clip("welding/weld_smoke_01", "CAM-WLD-01", sc.get("duration_s", 16) + 6, smoke_frame,
                      ["commons_welding_robot"],
                      f"{t_rstop:g} 秒起火花移除（模擬機器人停止，影像修補）；**合成煙霧**：{t_smoke:g} 秒起於工位外左後方疊加程式產生的煙霧"
                      "（非真實煙霧）；其餘同 normal_01", tag=lambda t: tag_weld, seed=32))

    # 點銲站（CAM-WLD-02，只看畫面）
    sp = [Shot("commons_fanuc_reaching", 0, 40, (1.0, 1.05), (0.5, 0.5), (0.52, 0.5))]
    clips.append(Clip("welding/spot_01", "CAM-WLD-02", 40, sequence(sp), ["commons_fanuc_reaching"],
                      "16:9 裁切、緩慢推拉、雜訊與壓縮、疊加時間與標示", seed=33))
    return clips


# ── 輸出 ────────────────────────────────────────────────────────
def write_clip(c: Clip, preview: list[float] | None = None) -> Path:
    out = VIDEOS / f"{c.name}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    look = Look(c.seed, **c.look)
    if preview is not None:
        for t in preview:
            img = overlay(look(c.render(t)), c.camera, t, c.tag(t))
            p = CACHE / "preview" / f"{c.name.replace('/', '_')}_{t:05.1f}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(p), img)
            print("預覽", p)
        return out
    tmp = out.with_suffix(".raw.mp4")
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    n = int(round(c.duration * FPS))
    for i in range(n):
        t = i / FPS
        vw.write(overlay(look(c.render(t)), c.camera, t, c.tag(t)))
    vw.release()
    if shutil.which("ffmpeg"):                     # 轉 H.264（瀏覽器也能播）；沒有 ffmpeg 就保留 mp4v
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-c:v", "libx264", "-preset", "medium",
                            "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
        if r.returncode == 0:
            tmp.unlink()
        else:
            tmp.replace(out)
    else:
        tmp.replace(out)
    print(f"{out.relative_to(PROTO)}  {c.duration:g}s  {n} 幀")
    return out


def write_sources(clips: list[Clip]) -> None:
    rows = []
    for c in clips:
        for k in c.sources:
            s = SOURCES[k]
            rows.append({"file": f"assets/videos/{c.name}.mp4", "camera": c.camera, "source_key": k,
                         "source_url": s.page, "author": s.author or "Pexels 攝影師（見原頁；--refresh-meta 補姓名）", "license": s.license,
                         "modifications": c.edits, "synthetic_edit": ("anomaly" if "合成" in c.edits else "motion" if "程式" in c.edits else "no")})
    with (VIDEOS / "SOURCES.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("寫入 assets/videos/SOURCES.csv", len(rows), "列")


def refresh_pexels_meta() -> None:
    """從 SH17 的 meta-data/（Kaggle 公開下載，免登入；會限流）取 Pexels 原頁與攝影師，存到 assets/videos/pexels_meta.json。"""
    meta = json.loads(PEXELS_META_FILE.read_text()) if PEXELS_META_FILE.exists() else {}
    base = "https://www.kaggle.com/api/v1/datasets/download/mugheesahmad/sh17-dataset-for-ppe-detection/meta-data%2F"
    for s in SOURCES.values():
        if s.kind != "pexels" or s.ref in meta:
            continue
        try:
            m = json.loads(_curl(f"{base}pexels-photo-{s.ref}.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            print(f"{s.ref}：Kaggle 限流或找不到，稍後再試")
            continue
        meta[s.ref] = {"url": m.get("url"), "photographer": m.get("photographer"),
                       "photographer_url": m.get("photographer_url"), "alt": m.get("alt")}
        time.sleep(2)
    PEXELS_META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"pexels_meta.json：{len(meta)} 筆")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="只產生這些影片（逗號分隔，例：ppe_violation_01,feed_jam_01）")
    ap.add_argument("--preview", help="不寫影片，只輸出這些秒數的預覽圖（逗號分隔）到快取資料夾")
    ap.add_argument("--no-fetch", action="store_true", help="不連網（來源照片需已在快取）")
    ap.add_argument("--sources-only", action="store_true", help="只重寫 SOURCES.csv，不產生影片")
    ap.add_argument("--refresh-meta", action="store_true", help="向 SH17 meta-data 補抓 Pexels 攝影師姓名")
    a = ap.parse_args()
    if a.refresh_meta:
        refresh_pexels_meta()
    if not a.no_fetch:
        fetch_sources(set(SOURCES))
    else:
        fetch_sources_offline()
    clips = build_clips()
    if a.sources_only:
        write_sources(clips)
        return
    if a.only:
        want = set(a.only.split(","))
        clips = [c for c in clips if c.name.split("/")[-1] in want or c.name in want]
    preview = [float(x) for x in a.preview.split(",")] if a.preview else None
    for c in clips:
        write_clip(c, preview)
    if preview is None and not a.only:
        write_sources(clips)


def fetch_sources_offline() -> None:
    """不連網：只從快取讀 metadata。"""
    meta_path = CACHE / "commons_meta.json"
    cmeta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    pmeta = json.loads(PEXELS_META_FILE.read_text()) if PEXELS_META_FILE.exists() else {}
    for s in SOURCES.values():
        if s.kind == "commons" and s.ref in cmeta:
            m = cmeta[s.ref]
            s.page, s.license, s.author = m["page"], m["license"], m["author"]
        elif s.kind == "pexels":
            m = pmeta.get(s.ref, {})
            s.page = m.get("url") or f"https://www.pexels.com/photo/{s.ref}/"
            s.author, s.license = m.get("photographer", ""), PEXELS_LICENSE


if __name__ == "__main__":
    main()
