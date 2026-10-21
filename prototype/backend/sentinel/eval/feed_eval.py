"""E5：Jev 看圖 V2（送料卡料／煙霧）在公開照片上的零樣本測試。

真實卡料照片取不到。改用 Wikimedia Commons 上授權清楚的沖床／送料照片（逐檔記錄網址、作者、授權），
全部是「正常」畫面（預期 jammed=false、smoke=false）；另外對 1 張送料機照片（Air Feeder.jpg，銅帶進模）
用影像編輯做「合成卡料」：以 HSV 切出銅帶，複製後旋轉翹起／錯位疊料貼回 → 標為合成，預期 jammed=true。

下載用 curl（Wikimedia 會擋 httpx 的 TLS 指紋，回 403 robot policy）。影像放本機暫存，不進 repo。
用法：python -m sentinel.eval.feed_eval [fetch] run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import subprocess
import time
import urllib.parse
from typing import Any

import cv2
import numpy as np

from ..jev import vision as V
from ..jev.client import JevUnavailable
from . import common as C
from .judge_eval import RawLog

DIR = C.SCRATCH / "commons"
UA = "SentinelEval/0.1 (student competition research; low volume)"
TITLES = ["File:176 TON SINGLE POINT STRAIGHT SIDE WITH SERVO COIL LINE.jpg", "File:SP2-440-Progressive Die Press.jpg",
          "File:HDP-176-Sutherland Two Point Straight Side-Cropped.jpg", "File:HDP-440-Servo Hydraulic Sutherland Straight Side.jpg",
          "File:Air Feeder.jpg", "File:Punch press room. (7f14e763-ee01-4676-b3af-4024f294fc12).jpg",
          "File:Watch stamping press.jpg", "File:Metal Stamping Project (5974754513).jpg",
          "File:Virginia Ludwig goes to work on a punch press 8b06152v.jpg", "File:Rejlek Metal & Stamping, 2017 Mór.jpg",
          "File:Punch Press Holecut 40-6CNC.jpg", "File:JMT JM-S Press Brake.jpg"]
# 人工目視分組（contact sheet 檢查）
GROUP = {
    "File:176 TON SINGLE POINT STRAIGHT SIDE WITH SERVO COIL LINE.jpg": "press_feed",
    "File:Air Feeder.jpg": "press_feed",
    "File:HDP-176-Sutherland Two Point Straight Side-Cropped.jpg": "press_feed",
    "File:HDP-440-Servo Hydraulic Sutherland Straight Side.jpg": "press_feed",
    "File:SP2-440-Progressive Die Press.jpg": "press_feed",
    "File:Metal Stamping Project (5974754513).jpg": "press_feed",
    "File:Virginia Ludwig goes to work on a punch press 8b06152v.jpg": "press_feed",
    "File:JMT JM-S Press Brake.jpg": "other_machine",
    "File:Punch Press Holecut 40-6CNC.jpg": "other_machine",
    "File:Watch stamping press.jpg": "other_machine",
    "File:Punch press room. (7f14e763-ee01-4676-b3af-4024f294fc12).jpg": "excluded",   # 火災後廢墟，不是正常機台畫面
    "File:Rejlek Metal & Stamping, 2017 Mór.jpg": "excluded",                           # 廠房外觀，看不到機台
}
REPEATS = 3


def _curl(url: str, out: str | None = None) -> bytes:
    r = subprocess.run(["curl", "-s", "-L", "-A", UA, url] + (["-o", out] if out else []), capture_output=True, check=True)
    return r.stdout


def fetch() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(0, len(TITLES), 6):
        q = urllib.parse.urlencode({"action": "query", "format": "json", "titles": "|".join(TITLES[i:i + 6]),
                                    "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1280,
                                    "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|ImageDescription"})
        d = json.loads(_curl("https://commons.wikimedia.org/w/api.php?" + q))
        for p in d["query"]["pages"].values():
            ii = p["imageinfo"][0]
            m = ii["extmetadata"]
            g = lambda k: re.sub(r"\s+", " ", re.sub("<[^>]+>", "", m.get(k, {}).get("value", ""))).strip()  # noqa: E731
            rec = {"title": p["title"], "page": ii["descriptionurl"], "width": ii["width"], "height": ii["height"],
                   "license": g("LicenseShortName"), "license_url": g("LicenseUrl"), "author": g("Artist")[:120],
                   "description": g("ImageDescription")[:300], "file": f"wm_{len(out):02d}.jpg"}
            _curl(ii["thumburl"], str(DIR / rec["file"]))
            time.sleep(1)
            out.append(rec)
    (DIR / "manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def strip_mask(img: np.ndarray) -> np.ndarray:
    """Air Feeder.jpg 的銅帶：橘色、中高飽和；只取右半部進料段。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (5, 60, 120), (22, 200, 255))
    h, w = m.shape
    m[:, : int(w * 0.44)] = 0
    m[: int(h * 0.33)] = 0
    m[int(h * 0.72):] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return m


def synth_jams(src: np.ndarray) -> dict[str, np.ndarray]:
    """合成卡料：銅帶複製後以進模點為軸旋轉（翹起歪斜）／平移疊料，貼回原圖。"""
    m = strip_mask(src)
    ys, xs = np.nonzero(m)
    pivot = (float(xs.min()), float(np.median(ys[xs < xs.min() + 20])))
    out = {}
    for name, ang, dx, dy in (("tilt_up_20", 20, 0, 0), ("tilt_up_32", 32, 0, 0), ("tilt_down_12", -12, 0, 0),
                              ("stacked_offset", 6, -40, -38)):
        M = cv2.getRotationMatrix2D(pivot, ang, 1.0)
        M[0, 2] += dx
        M[1, 2] += dy
        h, w = m.shape
        rm = cv2.warpAffine(m, M, (w, h))
        rimg = cv2.warpAffine(src, M, (w, h))
        img = src.copy()
        if name != "stacked_offset":          # 翹起：原位置的銅帶換成背景（鄰近像素修補）
            img = cv2.inpaint(img, cv2.dilate(m, np.ones((5, 5), np.uint8)), 7, cv2.INPAINT_TELEA)
        a = (cv2.GaussianBlur(rm, (5, 5), 0).astype(np.float32) / 255)[..., None]
        shade = 0.85 if ang > 0 else 1.0
        img = (img * (1 - a) + rimg * shade * a).astype(np.uint8)
        out[name] = img
    return out


async def run() -> None:
    man = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
    log = RawLog("e5_feed.jsonl")
    jev = C.jev_client(timeout_s=20)
    await jev.warmup()
    items: list[tuple[str, np.ndarray, dict]] = []
    for r in man:
        grp = GROUP.get(r["title"], "excluded")
        if grp == "excluded":
            continue
        items.append((r["file"], cv2.imread(str(DIR / r["file"])), {"group": grp, "expect_jam": False, "synthetic": False}))
        if r["title"] == "File:Air Feeder.jpg":
            for name, img in synth_jams(cv2.imread(str(DIR / r["file"]))).items():
                fn = f"synth_jam_{name}.jpg"
                cv2.imwrite(str(DIR / fn), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                items.append((fn, img, {"group": "synthetic_jam", "expect_jam": True, "synthetic": True, "base": r["file"]}))
    sem = asyncio.Semaphore(3)

    async def one(fn: str, img: np.ndarray, meta: dict, rep: int) -> None:
        key = f"feed:{fn}:{rep}"
        if log.has(key):
            return
        async with sem:
            row: dict[str, Any] = {"key": key, "file": fn, "rep": rep, **meta}
            try:
                j = await V.feed(jev, img, None, "沖床送料區（公開照片）", max_side=768)   # 正式 V2 題目與縮放
                row |= {"p_jam": j.p_jam, "p_smoke": j.p_smoke, "box": j.box, "box_conf": j.box_conf,
                        "latency_ms": j.latency_ms, "cost": j.cost}
            except JevUnavailable as e:
                row["error"] = str(e)
            log.add(row)

    await asyncio.gather(*(one(fn, img, meta, r) for fn, img, meta in items for r in range(REPEATS)))
    await jev.aclose()


def analyze() -> dict:
    man = {r["file"]: r for r in json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))}
    rows = list(RawLog("e5_feed.jsonl").rows.values())
    thr = C.SETTINGS.thresholds.p_jam
    per: dict[str, list[dict]] = {}
    for r in rows:
        per.setdefault(r["file"], []).append(r)
    imgs = []
    for fn, rs in sorted(per.items()):
        pj = [r["p_jam"] for r in rs]
        ps = [r["p_smoke"] for r in rs]
        src = man.get(fn) or man.get(rs[0].get("base", ""), {})
        imgs.append({"file": fn, "group": rs[0]["group"], "synthetic": rs[0]["synthetic"], "expect_jam": rs[0]["expect_jam"],
                     "p_jam_runs": [round(x, 3) for x in pj], "p_smoke_runs": [round(x, 3) for x in ps],
                     "jam_pred_all_runs": all(x > thr for x in pj), "jam_pred_any_run": any(x > thr for x in pj),
                     "title": src.get("title"), "page": src.get("page"), "license": src.get("license"),
                     "author": src.get("author"), "derived_from": rs[0].get("base")})

    def grp(g):
        return [i for i in imgs if i["group"] == g]
    normal = grp("press_feed") + grp("other_machine")
    syn = grp("synthetic_jam")
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    cost = [r["cost"] for r in rows if r.get("cost") is not None]
    out = {
        "description": "Jev V2（正式 FEED_QUESTIONS）零樣本：公開正常照片＋合成卡料（標示合成）；每張 3 次",
        "threshold_p_jam": thr, "rule": "正式規則：連續 2 張 p_jam > 0.7 才成立；這裡用「3 次都 > 0.7」近似",
        "n_images": {"press_feed_normal": len(grp("press_feed")), "other_machine_normal": len(grp("other_machine")),
                     "synthetic_jam": len(syn), "excluded": [t for t, g in GROUP.items() if g == "excluded"]},
        "normal_false_jam": C.rate(sum(i["jam_pred_all_runs"] for i in normal), len(normal)),
        "normal_false_jam_any_run": C.rate(sum(i["jam_pred_any_run"] for i in normal), len(normal)),
        "normal_false_smoke_any_run": C.rate(sum(any(x > 0.5 for x in i["p_smoke_runs"]) for i in normal), len(normal)),
        "synthetic_jam_detected": C.rate(sum(i["jam_pred_all_runs"] for i in syn), len(syn)),
        "p_jam_mean": {"normal": round(statistics.fmean(x for i in normal for x in i["p_jam_runs"]), 3) if normal else None,
                       "synthetic_jam": round(statistics.fmean(x for i in syn for x in i["p_jam_runs"]), 3) if syn else None},
        "latency_ms": C.lat_stats(lat), "cost_usd": {"total": round(sum(cost), 6),
                                                    "per_call": round(statistics.fmean(cost), 7) if cost else None},
        "errors": [r.get("error") for r in RawLog("e5_feed.jsonl").rows.values() if r.get("error")],
        "images": imgs,
        "cannot_validate": ["真實卡料（板材堆疊、翹起、卡在送料口）的偵測率：沒有真實卡料照片，合成圖只是單一張照片的影像編輯",
                            "送料口 ROI 裁切：公開照片角度各異，這裡送整張圖（正式流程只送 feed_inlet ROI）",
                            "煙霧：沒有沖壓線煙霧照片，只測了正常畫面的誤報"],
    }
    C.save_json("e5_feed_jev.json", out)
    return out


def copy_samples() -> None:
    """報告用：1 張原圖＋1 張合成（Air Feeder.jpg，CC BY-SA 3.0，Jstokes452）。改作沿用 CC BY-SA。"""
    samples = C.EVAL_DIR / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    man = {r["title"]: r for r in json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))}
    r = man["File:Air Feeder.jpg"]
    for src, dst in ((r["file"], "commons_air_feeder.jpg"), ("synth_jam_tilt_up_20.jpg", "commons_air_feeder_合成卡料.jpg")):
        img = cv2.imread(str(DIR / src))
        s = min(1.0, 480 / max(img.shape[:2]))
        cv2.imwrite(str(samples / dst), cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s))),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    p = samples / "ATTRIBUTION.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    if "Air Feeder" not in txt:
        txt += ("\n# Wikimedia Commons\n\n| 檔名 | 說明 | 原圖 | 作者 | 授權 |\n|---|---|---|---|---|\n"
                f"| commons_air_feeder.jpg | 原圖縮小 | {r['page']} | {r['author']} | {r['license']} |\n"
                f"| commons_air_feeder_合成卡料.jpg | **合成**：銅帶切出後旋轉 20° 貼回（模擬翹起），非真實卡料 | {r['page']} | {r['author']}（改作：Sentinel AI 團隊） | {r['license']} |\n")
        p.write_text(txt, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmds", nargs="*", default=["run"])
    a = ap.parse_args(argv)
    if "fetch" in a.cmds or not (DIR / "manifest.json").exists():
        fetch()
    if "run" in a.cmds:
        asyncio.run(run())
    r = analyze()
    copy_samples()
    print(json.dumps({k: r[k] for k in ("normal_false_jam", "synthetic_jam_detected", "p_jam_mean")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
