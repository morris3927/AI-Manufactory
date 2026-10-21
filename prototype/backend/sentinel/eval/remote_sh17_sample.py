"""在訓練伺服器上執行（只用 CPU）：從 SH17 val 抽頭部裁切圖與整張圖，供 E4（Jev V1）使用。

這支檔案會被 scp 到伺服器上用系統 python3 執行；只依賴 opencv、標準函式庫。
SH17 類別：10 = helmet、12 = head（YOLO 格式，正規化 cx cy w h）。

用法：python3 remote_sh17_sample.py <SH17 根目錄> <輸出目錄> [每類數量=60] [整張圖數量=20]
"""
import json
import os
import random
import sys

import cv2

HEAD, HELMET = 12, 10
SEED = 20261021


def read_labels(p, W, H):
    out = []
    if not os.path.exists(p):
        return out
    for line in open(p):
        f = line.split()
        if len(f) != 5:
            continue
        c, cx, cy, w, h = int(f[0]), *map(float, f[1:])
        out.append((c, [(cx - w / 2) * W, (cy - h / 2) * H, (cx + w / 2) * W, (cy + h / 2) * H]))
    return out


def inter(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def area(a):
    return max(0, a[2] - a[0]) * max(0, a[3] - a[1])


def main():
    root, out_dir = sys.argv[1], sys.argv[2]
    n_per = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    n_full = int(sys.argv[4]) if len(sys.argv) > 4 else 20
    os.makedirs(os.path.join(out_dir, "crops"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "full"), exist_ok=True)
    names = [x.strip() for x in open(os.path.join(root, "val_files.txt")) if x.strip()]
    rnd = random.Random(SEED)
    rnd.shuffle(names)
    pos, neg, full = [], [], []
    for name in names:
        if len(pos) >= n_per and len(neg) >= n_per and len(full) >= n_full:
            break
        img_p = os.path.join(root, "images", name)
        lab_p = os.path.join(root, "labels", os.path.splitext(name)[0] + ".txt")
        img = cv2.imread(img_p)
        if img is None:
            continue
        H, W = img.shape[:2]
        labs = read_labels(lab_p, W, H)
        heads = [b for c, b in labs if c == HEAD]
        helmets = [b for c, b in labs if c == HELMET]
        if not heads:
            continue

        def ov(h):
            return max((inter(h, m) / area(h) for m in helmets), default=0.0) if area(h) else 0.0

        meta_p = os.path.join(root, "meta-data", os.path.splitext(name)[0] + ".json")
        meta = {}
        if os.path.exists(meta_p):
            try:
                m = json.load(open(meta_p))
                meta = {"url": m.get("url"), "photographer": m.get("photographer")}
            except Exception:
                pass
        # 整張圖（Box 測試）：只有 1 個頭、沒有安全帽
        if len(full) < n_full and len(heads) == 1 and ov(heads[0]) < 0.05 and not helmets:
            hb = heads[0]
            if min(hb[2] - hb[0], hb[3] - hb[1]) >= 0.04 * min(W, H):
                s = min(1.0, 1600 / max(W, H))
                im2 = cv2.resize(img, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA)
                fn = f"full_{len(full):02d}.jpg"
                cv2.imwrite(os.path.join(out_dir, "full", fn), im2, [cv2.IMWRITE_JPEG_QUALITY, 90])
                full.append({"file": fn, "src": name, "W": W, "H": H,
                             "head_box_norm": [hb[0] / W, hb[1] / H, hb[2] / W, hb[3] / H], **meta})
        # 頭部裁切（每張圖最多取 1 個頭，增加多樣性）
        rnd.shuffle(heads)
        for hb in heads:
            o = ov(hb)
            label = "helmet" if o >= 0.3 else ("no_helmet" if o < 0.05 else None)
            if label is None:
                continue
            if label == "helmet" and len(pos) >= n_per or label == "no_helmet" and len(neg) >= n_per:
                continue
            w, h = hb[2] - hb[0], hb[3] - hb[1]
            if min(w, h) < 48:                           # 太小的頭不取
                continue
            # 頭框外擴：左右各 0.8 倍寬、上 0.6 倍高、下 1.5 倍高（帶到肩膀，像 YOLO 人員框的上半身）
            x1, x2 = max(0, hb[0] - 0.8 * w), min(W, hb[2] + 0.8 * w)
            y1, y2 = max(0, hb[1] - 0.6 * h), min(H, hb[3] + 1.5 * h)
            crop = img[int(y1):int(y2), int(x1):int(x2)]
            if crop.size == 0:
                continue
            s = min(1.0, 1024 / max(crop.shape[:2]))
            if s < 1:
                crop = cv2.resize(crop, (round(crop.shape[1] * s), round(crop.shape[0] * s)), interpolation=cv2.INTER_AREA)
            lst = pos if label == "helmet" else neg
            fn = f"{label}_{len(lst):02d}.jpg"
            cv2.imwrite(os.path.join(out_dir, "crops", fn), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            lst.append({"file": fn, "label": label, "src": name, "helmet_overlap": round(o, 3),
                        "head_px": [round(w), round(h)], "crop_px": [crop.shape[1], crop.shape[0]], **meta})
            break
    json.dump({"seed": SEED, "crops": pos + neg, "full": full}, open(os.path.join(out_dir, "manifest.json"), "w"),
              ensure_ascii=False, indent=1)
    print(len(pos), len(neg), len(full))


if __name__ == "__main__":
    main()
