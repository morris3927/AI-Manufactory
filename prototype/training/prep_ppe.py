"""把 SH17 與 Hard Hat Workers 合併成 2 類（head、helmet）的 YOLO 資料集。

為什麼只留 2 類：Hard Hat Workers 的 person 標註很稀疏，若保留 person，
沒被標到的人會被當成背景，反而拉低 person 的準確度。系統只需要「頭有沒有安全帽」，
送 Jev 確認的裁切圖也只取頭部區域，所以 2 類就夠。

授權：SH17 為 CC BY-NC-SA 4.0（非商業）；Hard Hat Workers（Voxel51 版）為 CC0。

用法（訓練主機）：
    python prep_ppe.py --sh17 ~/.cache/kagglehub/datasets/mugheesahmad/sh17-dataset-for-ppe-detection/versions/1 \
                       --hardhat raw/hardhat --out data/ppe2
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

NAMES = {0: "head", 1: "helmet"}
SH17_MAP = {12: 0, 10: 1}                       # SH17：head=12、helmet=10
HARDHAT_MAP = {"head": 0, "helmet": 1}          # person 刻意不收
HARDHAT_VAL_RATIO = 0.2                         # 依檔名雜湊固定切分，可重現
# 注意：FiftyOne 的 _rand 是內部排序值（集中在 0.999～1），不能拿來切分


def link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        os.symlink(src.resolve(), dst)


def prep_sh17(root: Path, out: Path) -> dict:
    counts = {}
    for split, listfile in (("train", "train_files.txt"), ("val", "val_files.txt")):
        n = 0
        for name in (root / listfile).read_text().split():
            img = root / "images" / name
            lab = root / "labels" / (Path(name).stem + ".txt")
            if not img.exists() or not lab.exists():
                continue
            rows = []
            for line in lab.read_text().splitlines():
                parts = line.split()
                if parts and int(parts[0]) in SH17_MAP:
                    rows.append(" ".join([str(SH17_MAP[int(parts[0])]), *parts[1:]]))
            stem = f"sh17_{Path(name).stem}"
            link(img, out / "images" / split / f"{stem}{img.suffix}")
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split / f"{stem}.txt").write_text("\n".join(rows))
            n += 1
        counts[f"sh17_{split}"] = n
    return counts


def prep_hardhat(root: Path, out: Path) -> dict:
    samples = json.loads((root / "samples.json").read_text())["samples"]
    counts = {"hardhat_train": 0, "hardhat_val": 0}
    for s in samples:
        img = root / s["filepath"]
        if not img.exists():
            continue
        bucket = int(hashlib.md5(img.name.encode()).hexdigest(), 16) % 100
        split = "val" if bucket < HARDHAT_VAL_RATIO * 100 else "train"
        rows = []
        for d in (s.get("ground_truth") or {}).get("detections", []):
            cls = HARDHAT_MAP.get(d["label"].lower())
            if cls is None:
                continue
            x, y, w, h = d["bounding_box"]          # 左上角 + 寬高，已正規化
            rows.append(f"{cls} {x + w / 2:.6f} {y + h / 2:.6f} {w:.6f} {h:.6f}")
        stem = f"hh_{img.stem}"
        link(img, out / "images" / split / f"{stem}{img.suffix}")
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split / f"{stem}.txt").write_text("\n".join(rows))
        counts[f"hardhat_{split}"] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sh17", type=Path, required=True)
    ap.add_argument("--hardhat", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data/ppe2"))
    a = ap.parse_args()
    counts = prep_sh17(a.sh17, a.out) | prep_hardhat(a.hardhat, a.out)
    (a.out / "ppe2.yaml").write_text(
        f"path: {a.out.resolve()}\ntrain: images/train\nval: images/val\n"
        + "names:\n" + "".join(f"  {k}: {v}\n" for k, v in NAMES.items()))
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
