"""E4：Jev 看圖 V1（安全帽確認）＋ Box 定位，資料為 SH17 val（Pexels 公開照片，CC BY-NC-SA 4.0）。

步驟：
  1. fetch：把 remote_sh17_sample.py 送到訓練伺服器（只用 CPU）抽 60＋60 張頭部裁切與 20 張整張圖，複製回本機暫存
     （~/.cache/sentinel-eval/sentinel-eval-sh17，不放進 repo）
  2. run：送 Jev（正式 V1 題目＋任務指定的正向題目，各一次請求），整張圖問 Box
  3. analyze：準確率、Precision／Recall（正類＝沒戴安全帽）、AUROC、延遲、成本、Box IoU

用法：python -m sentinel.eval.ppe_eval [fetch] run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import json
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2

from ..jev import questions as Q
from ..jev.client import JevUnavailable
from ..jev.vision import fit_size, to_part
from . import common as C
from .judge_eval import RawLog

HOST = os.environ.get("SENTINEL_TRAIN_HOST", "train-host")   # 訓練主機的 ssh 名稱（設定於本機 ~/.ssh/config，不進 git）
SH17 = "~/.cache/kagglehub/datasets/mugheesahmad/sh17-dataset-for-ppe-detection/versions/1"
LOCAL = C.SCRATCH / "sentinel-eval-sh17"
SAMPLES = C.EVAL_DIR / "samples"

WEARS_Q = {"wears_helmet": {"type": "noul", "instructions": "圖中人員頭部是否戴有安全帽",
                            "criteria": {"true": "頭部戴著安全帽（工地帽、硬帽）", "false": "頭部沒有安全帽"}}}
BOX_Q = {"head_box": {"type": "box", "instructions": "未戴安全帽的人員頭部"}}
THR = 0.5
# 2026-10-21 實測：Jev 實際要求每邊 ≥ 280 px（279 → HTTP 400 "Invalid image dimensions"），
# 文件寫的 256 不正確。正式 vision.MIN_SIDE=256 → 小裁切圖會失敗。失敗的圖另外放大到短邊 288 重送（標為 fix）。
REAL_MIN_SIDE = 288


def upscale_min_side(img, min_side: int = REAL_MIN_SIDE):
    import numpy as np
    h, w = img.shape[:2]
    s = max(1.0, min_side / min(h, w))
    if max(h, w) * s > 1568:                     # 長寬比太極端：長邊壓到 1568，短邊補黑邊到 min_side
        s = 1568 / max(h, w)
    img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_LINEAR) if s != 1 else img
    h, w = img.shape[:2]
    if min(h, w) < min_side:
        canvas = np.zeros((max(h, min_side), max(w, min_side), 3), np.uint8)
        canvas[:h, :w] = img
        img = canvas
    return img


def fetch() -> None:
    script = Path(__file__).with_name("remote_sh17_sample.py")
    subprocess.run(["scp", "-q", str(script), f"{HOST}:/tmp/sentinel_sh17_sample.py"], check=True)
    subprocess.run(["ssh", "-o", "BatchMode=yes", HOST,
                    f'export CUDA_VISIBLE_DEVICES=""; nice -n 10 python3 /tmp/sentinel_sh17_sample.py {SH17} '
                    f'~/sentinel-eval-sh17 60 20; rm -f /tmp/sentinel_sh17_sample.py'], check=True)
    if LOCAL.exists():
        shutil.rmtree(LOCAL)
    subprocess.run(["scp", "-q", "-r", f"{HOST}:sentinel-eval-sh17", str(C.SCRATCH)], check=True)


def manifest() -> dict:
    return json.loads((LOCAL / "manifest.json").read_text(encoding="utf-8"))


async def run(conc: int = 3) -> None:
    m = manifest()
    log = RawLog("e4_ppe.jsonl")
    jev = C.jev_client(timeout_s=20)
    await jev.warmup()
    sem = asyncio.Semaphore(conc)

    async def ask(key: str, state: dict, questions: dict, meta: dict) -> None:
        if log.has(key):
            return
        async with sem:
            row: dict[str, Any] = {"key": key, **meta, "ts": time.time()}
            try:
                res = await jev.ask(state, questions, purpose="eval.ppe", questions_ver=meta.get("qver", ""))
                row["answers"] = {k: v.model_dump() for k, v in res.answers.items()}
                row["latency_ms"], row["cost"], row["input_tokens"] = res.latency_ms, res.cost, res.input_tokens
            except JevUnavailable as e:
                row["error"] = str(e)
            log.add(row)

    tasks = []
    for c in m["crops"]:
        img = cv2.imread(str(LOCAL / "crops" / c["file"]))
        part = to_part(img)                               # 與正式 vision.ppe 相同的縮放／編碼（max_side 768）
        h, w = img.shape[:2]
        nw, nh, pw, ph = fit_size(w, h, 768)
        meta = {"file": c["file"], "label": c["label"], "sent_px": [pw, ph]}
        # A：正式 V1 題目（PPE_QUESTIONS：no_helmet＋in_work_zone），state 與 vision.ppe 相同
        tasks.append(ask(f"prod:{c['file']}", {"area": "組裝線作業區", "person_image": part}, Q.PPE_QUESTIONS,
                         meta | {"variant": "prod", "qver": Q.PPE_VER}))
        # B：任務指定的正向題目「圖中人員頭部是否戴有安全帽」
        tasks.append(ask(f"wears:{c['file']}", {"person_image": part}, WEARS_Q, meta | {"variant": "wears", "qver": "eval-wears-1"}))
    await asyncio.gather(*tasks)
    # 失敗（HTTP 400 尺寸）的圖：放大到短邊 288 後重送
    tasks = []
    for c in m["crops"]:
        for variant, state_extra, qs in (("prod", {"area": "組裝線作業區"}, Q.PPE_QUESTIONS), ("wears", {}, WEARS_Q)):
            k = f"{variant}:{c['file']}"
            if log.has(k):
                continue
            img = upscale_min_side(cv2.imread(str(LOCAL / "crops" / c["file"])))
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            import base64
            part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}}
            tasks.append(ask(f"{variant}fix:{c['file']}", state_extra | {"person_image": part}, qs,
                             {"file": c["file"], "label": c["label"], "sent_px": [img.shape[1], img.shape[0]],
                              "variant": variant + "fix", "qver": "upscaled-288"}))
    for f in m["full"]:
        img = cv2.imread(str(LOCAL / "full" / f["file"]))
        h, w = img.shape[:2]
        nw, nh, pw, ph = fit_size(w, h, 1024)
        tasks.append(ask(f"box:{f['file']}", {"image": to_part(img, max_side=1024)}, BOX_Q,
                         {"variant": "box", "file": f["file"], "scale": [nw, nh, pw, ph], "qver": "eval-box-1"}))
    await asyncio.gather(*tasks)
    await jev.aclose()


def iou(a: list[float], b: list[float]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x2 - x1) * max(0, y2 - y1)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def binary_metrics(y: list[int], p: list[float], thr: float = THR) -> dict[str, Any]:
    pred = [int(x > thr) for x in p]
    tp = sum(1 for a, b in zip(y, pred) if a and b)
    fp = sum(1 for a, b in zip(y, pred) if not a and b)
    fn = sum(1 for a, b in zip(y, pred) if a and not b)
    tn = sum(1 for a, b in zip(y, pred) if not a and not b)
    pos = [q for a, q in zip(y, p) if a]
    neg = [q for a, q in zip(y, p) if not a]
    au = sum(1.0 if a > b else 0.5 if a == b else 0 for a in pos for b in neg) / (len(pos) * len(neg)) if pos and neg else None
    return {"n": len(y), "threshold": thr, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "accuracy": C.rate(tp + tn, len(y)),
            "precision_no_helmet": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall_no_helmet": round(tp / (tp + fn), 3) if tp + fn else None,
            "specificity": round(tn / (tn + fp), 3) if tn + fp else None,
            "auroc": round(au, 3) if au is not None else None}


def analyze() -> dict:
    m = manifest()
    log = RawLog("e4_ppe.jsonl")
    rows = list(log.rows.values())
    all_rows = [json.loads(x) for x in (C.RAW / "e4_ppe.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    out: dict[str, Any] = {
        "description": "Jev 1.14 V1 安全帽確認（SH17 val 頭部裁切）與 Box 定位（整張圖）",
        "dataset": {"name": "SH17 (Safe Human dataset, 17 classes)", "split": "val",
                    "license": "CC BY-NC-SA 4.0（影像來自 Pexels）", "seed": m["seed"],
                    "n_crops": len(m["crops"]), "n_helmet": sum(c["label"] == "helmet" for c in m["crops"]),
                    "n_no_helmet": sum(c["label"] == "no_helmet" for c in m["crops"]), "n_full": len(m["full"]),
                    "labeling": "head(12) 與 helmet(10) 框重疊／頭框面積 ≥ 0.3 → 有戴；< 0.05 → 沒戴；中間值不取",
                    "crop": "頭框外擴（左右 0.8w、上 0.6h、下 1.5h），經正式 vision.to_part 縮放（長邊 ≤ 768、每邊 ≥ 256）",
                    "face_blur": "未做（公開 Pexels 照片）；正式流程會先模糊臉部，可能影響結果"},
        "errors": [f"{r['variant']}:{r['file']}:{r.get('error')}" for r in all_rows if r.get("error")],
        "jev_real_min_side_px": {"documented": 256, "measured": 280,
                                 "probe": "隨機雜訊 JPEG：280×419、280×600、288×288 → 200；279×600、271×419、256×256、256×1000 → 400"},
    }
    for variant, key, invert in (("prod", "no_helmet", False), ("wears", "wears_helmet", True)):
        seen: dict[str, dict] = {}
        for r in all_rows:                      # 每個 key 只看第一次送出的結果（正式行為）
            if r["variant"] == variant and r["key"] not in seen:
                seen[r["key"]] = r
        first = list(seen.values())
        rs = [r for r in rows if r["variant"] in (variant, variant + "fix")]
        y = [int(r["label"] == "no_helmet") for r in rs]
        p = [(1 - r["answers"][key]["noul"]) if invert else r["answers"][key]["noul"] for r in rs]
        res = binary_metrics(y, p)
        res["http_400_as_production"] = {"failed": sum(1 for r in first if r.get("error")), "sent": len(first),
                                         "rate": round(sum(1 for r in first if r.get("error")) / len(first), 3) if first else None,
                                         "note": "正式 to_part（MIN_SIDE=256）送出時被拒；重送時放大到短邊 288（variant=*fix）後全部成功"}
        res["n_upscaled_retry"] = sum(1 for r in rs if r["variant"].endswith("fix"))
        res["latency_ms"] = C.lat_stats([r["latency_ms"] for r in rs])
        costs = [r["cost"] for r in rs if r.get("cost") is not None]
        res["cost_usd"] = {"total": round(sum(costs), 6), "per_call": round(statistics.fmean(costs), 7) if costs else None}
        res["input_tokens_mean"] = round(statistics.fmean(r["input_tokens"] for r in rs if r.get("input_tokens")), 0) if rs else None
        res["question"] = Q.PPE_QUESTIONS["no_helmet"]["instructions"] if variant == "prod" else WEARS_Q["wears_helmet"]["instructions"]
        if variant == "prod":
            res["p_in_work_zone_mean"] = round(statistics.fmean(r["answers"]["in_work_zone"]["noul"] for r in rs), 3)
        res["errors_list"] = [{"file": r["file"], "label": r["label"], "p_no_helmet": round(q, 3)}
                              for r, q in zip(rs, p) if int(q > THR) != int(r["label"] == "no_helmet")]
        out[variant] = res
    # Box
    gt = {f["file"]: f for f in m["full"]}
    ious, lat, cost, per = [], [], [], []
    for r in rows:
        if r["variant"] != "box":
            continue
        b = r["answers"]["head_box"]["box"]
        nw, nh, pw, ph = r["scale"]
        # 回傳座標相對於送出的（可能補邊的）圖 → 換回原圖正規化座標
        pred = [b["left"] * pw / nw, b["top"] * ph / nh, b["right"] * pw / nw, b["bottom"] * ph / nh]
        g = gt[r["file"]]["head_box_norm"]
        v = iou(pred, g)
        ious.append(v)
        lat.append(r["latency_ms"])
        cost.append(r.get("cost") or 0)
        cx, cy = (pred[0] + pred[2]) / 2, (pred[1] + pred[3]) / 2
        per.append({"file": r["file"], "iou": round(v, 3), "pred": [round(x, 3) for x in pred], "gt": [round(x, 3) for x in g],
                    "center_in_gt": g[0] <= cx <= g[2] and g[1] <= cy <= g[3], "conf": r["answers"]["head_box"].get("confidence")})
    out["box"] = {"question": BOX_Q["head_box"]["instructions"], "n": len(ious),
                  "iou_mean": round(statistics.fmean(ious), 3) if ious else None,
                  "iou_median": round(statistics.median(ious), 3) if ious else None,
                  "iou_ge_0_5": C.rate(sum(v >= 0.5 for v in ious), len(ious)),
                  "iou_ge_0_3": C.rate(sum(v >= 0.3 for v in ious), len(ious)),
                  "center_in_gt": C.rate(sum(p["center_in_gt"] for p in per), len(per)),
                  "latency_ms": C.lat_stats(lat), "cost_usd": {"total": round(sum(cost), 6),
                                                               "per_call": round(statistics.fmean(cost), 7) if cost else None},
                  "per_image": per}
    C.save_json("e4_ppe_jev.json", out)
    return out


def copy_samples(n_each: int = 3) -> None:
    """報告用小樣本（≤ 6 張）＋出處說明。"""
    m = manifest()
    SAMPLES.mkdir(parents=True, exist_ok=True)
    picked = [c for c in m["crops"] if c["label"] == "helmet"][:n_each] + [c for c in m["crops"] if c["label"] == "no_helmet"][:n_each]
    lines = ["# 報告用樣本（SH17 val 頭部裁切）", "",
             "來源：SH17 dataset（Ahmad et al., 2024，Kaggle mugheesahmad/sh17-dataset-for-ppe-detection），授權 CC BY-NC-SA 4.0；",
             "原始照片來自 Pexels（下列為原頁面與攝影師）。只供非商業的競賽報告使用，改作（裁切、縮放）沿用相同授權。", "",
             "| 檔名 | 標註 | 原圖 | 攝影師 |", "|---|---|---|---|"]
    for c in picked:
        img = cv2.imread(str(LOCAL / "crops" / c["file"]))
        s = min(1.0, 384 / max(img.shape[:2]))
        img = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(SAMPLES / f"sh17_{c['file']}"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        lines.append(f"| sh17_{c['file']} | {'有戴安全帽' if c['label'] == 'helmet' else '沒戴安全帽'} | {c.get('url', '-')} | {c.get('photographer', '-')} |")
    (SAMPLES / "ATTRIBUTION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmds", nargs="*", default=["run"], help="fetch／run／analyze")
    a = ap.parse_args(argv)
    if "fetch" in a.cmds or not (LOCAL / "manifest.json").exists():
        fetch()
    if "run" in a.cmds:
        asyncio.run(run())
    r = analyze()
    copy_samples()
    print(json.dumps({k: r[k]["accuracy"] for k in ("prod", "wears")} | {"box_iou_mean": r["box"]["iou_mean"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
