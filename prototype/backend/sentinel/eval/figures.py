"""產生成果說明書圖表（eval/results/figures/*.png）與 E7 結果檔。只讀 results/*.json，不打 API。

用法：python -m sentinel.eval.figures
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from . import common as C  # noqa: E402

# 參考調色盤（dataviz palette.md 前 3 個 slot，all-pairs 驗證通過）＋中性灰
JEV, LLM, RULE = "#2a78d6", "#eb6834", "#1baf7a"
GRAY, INK, INK2, GRID = "#9a9993", "#0b0b0b", "#52514e", "#e6e5e0"

for f in ("PingFang TC", "Heiti TC", "Noto Sans TC", "Arial Unicode MS"):
    if any(x.name == f for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f
        break
plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150, "font.size": 10,
                     "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlecolor": INK})


def _save(fig, name: str) -> None:
    fig.tight_layout()
    fig.savefig(C.FIGURES / name, facecolor="white")
    plt.close(fig)


def e7() -> dict:
    """E7：PPE YOLO 訓練結果（由訓練伺服器回報，本評估未重跑）。"""
    out = {"description": "PPE 偵測器 prototype/models/ppe.pt（YOLOv8s，2 類 head／helmet，訓練資料 SH17＋HardHat）",
           "source": "訓練伺服器 ~/sentinel-train 的驗證結果，由協調者轉述；本評估未重新計算",
           "inference_ms_per_img": {"value": 2.4, "hardware": "RTX 2080 Ti"},
           "sh17_val": {"ours": {"head": {"P": 0.955, "R": 0.855, "mAP50": 0.901}, "helmet": {"P": 0.757, "R": 0.585, "mAP50": 0.691}},
                        "baseline_sh17_yolov8s": {"head": {"mAP50": 0.878}, "helmet": {"P": 0.838, "R": 0.532, "mAP50": 0.666}}},
           "hardhat_val": {"ours": {"head": {"mAP50": 0.924}, "helmet": {"P": 0.95, "R": 0.922, "mAP50": 0.965}}},
           "caveats": ["SH17 helmet 召回 0.585：約四成安全帽漏檢 → 頭部被判成「沒戴」，這正是 V1（Jev 看圖確認）要過濾的誤報來源",
                       "E6 執行時（2026-10-21 00:20–00:30）ppe.pt 尚未放入（檔案時間 09:12），PPE 劇本用的是劇本重播偵測（scripted）"]}
    C.save_json("e7_ppe_yolo.json", out)
    return out


def fig_comparison(e2: dict) -> None:
    methods = [("純規則", e2["rules"], RULE), ("Jev 1.14", e2["jev"], JEV), ("DeepSeek V4.1 Flash", e2["llm"], LLM)]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for i, (name, d, col) in enumerate(methods):
        r = d["root_cause_top1"]
        lo, hi = r["ci95"]
        ax.bar(i, r["rate"], width=0.55, color=col, zorder=2)
        ax.errorbar(i, r["rate"], yerr=[[r["rate"] - lo], [hi - r["rate"]]], color=INK2, capsize=4, lw=1, zorder=3)
        ax.text(i, hi + 0.02, f"{r['rate']:.1%}\n({r['k']}/{r['n']})", ha="center", va="bottom", fontsize=9, color=INK)
    ax.axhline(e2["jev"].get("chance", 0.117), color=GRAY, lw=1, ls="--", zorder=1)
    ax.text(2.45, 0.13, "隨機猜 11.7%", color=INK2, fontsize=8, ha="right")
    ax.set_xticks(range(3), [m[0] for m in methods])
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("根因 Top-1 準確率")
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax.set_title("保留測試集根因判斷（37 個不同案例，誤差線 95% Wilson CI）")
    _save(fig, "e2_top1_comparison.png")


def fig_latency(e2: dict) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    rows = [("Jev 1.14", e2["jev"]["latency_ms"], JEV), ("DeepSeek V4.1 Flash", e2["llm"]["latency_ms"], LLM)]
    for i, (name, d, col) in enumerate(rows):
        ax.barh(i, d["p50"] / 1000, height=0.5, color=col, zorder=2)
        ax.plot([d["p95"] / 1000], [i], marker="|", ms=16, mew=2, color=INK2, zorder=3)
        ax.text(d["p95"] / 1000 * 1.15, i, f"P50 {d['p50'] / 1000:.2f} s｜P95 {d['p95'] / 1000:.1f} s", va="center", fontsize=9)
    ax.set_xscale("log")
    ax.set_xlim(0.1, 300)
    ax.set_yticks(range(2), [r[0] for r in rows])
    ax.set_xlabel("每次判斷延遲（秒，對數軸；長條＝P50，刻線＝P95）")
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.set_title("同一份 state、同一組 4 題的延遲（40 次）")
    _save(fig, "e2_latency.png")


def fig_reliability(e3: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 4.0), sharey=True)
    for ax, key, title in ((axes[0], "heldout_per_case_mean", "保留測試集（37 案例，每案 3 次取平均）"),
                           (axes[1], "synthetic", "合成事件（70 筆，骨架產生器，標示合成）")):
        d = e3[key]
        ax.plot([0, 1], [0, 1], color=GRAY, ls="--", lw=1, label="完美校準")
        xs = [b["p_pred"] for b in d["bins"] if b["n"]]
        ys = [b["p_obs"] for b in d["bins"] if b["n"]]
        ns = [b["n"] for b in d["bins"] if b["n"]]
        ci = [b["ci95"] for b in d["bins"] if b["n"]]
        ax.errorbar(xs, ys, yerr=[[y - c[0] for y, c in zip(ys, ci)], [c[1] - y for y, c in zip(ys, ci)]],
                    fmt="o-", color=JEV, ms=7, lw=2, capsize=3, mec="white", mew=1.5, label="Jev top-1 機率")
        for x, y, n in zip(xs, ys, ns):
            ax.annotate(f"n={n}", (x, y), textcoords="offset points", xytext=(6, -12), fontsize=8, color=INK2)
        ax.set_xlim(0, 1.02)
        ax.set_ylim(-0.02, 1.08)
        ax.set_xlabel("Jev 給的 top-1 機率（5 組）")
        ax.set_title(title, fontsize=10)
        ax.grid(color=GRID, lw=0.8)
        ax.text(0.03, 0.93, f"ECE {d['ece']:.3f}｜Brier {d['brier']:.3f}", transform=ax.transAxes, fontsize=9, color=INK)
    axes[0].set_ylabel("實際準確率")
    axes[0].legend(loc="upper left", bbox_to_anchor=(0, 0.9), fontsize=8, frameon=False)
    fig.suptitle("E3 根因機率校準曲線（reliability diagram）", fontweight="bold")
    _save(fig, "e3_reliability.png")


def fig_ppe(e4: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.4), gridspec_kw={"width_ratios": [1, 1.3]})
    d = e4["wears"]
    m = [[d["tn"], d["fp"]], [d["fn"], d["tp"]]]
    ax = axes[0]
    ax.imshow([[1, 0], [0, 1]], cmap=matplotlib.colors.ListedColormap(["#f3f2ee", "#d6e4f7"]))
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(m[i][j]), ha="center", va="center", fontsize=16, color=INK)
    ax.set_xticks([0, 1], ["判：有戴", "判：沒戴"])
    ax.set_yticks([0, 1], ["實際有戴", "實際沒戴"])
    ax.set_title(f"V1 安全帽確認（{d['n']} 張 SH17 頭部裁切）", fontsize=10)
    for s in ax.spines.values():
        s.set_visible(False)
    ax = axes[1]
    ious = sorted(p["iou"] for p in e4["box"]["per_image"])
    ax.bar(range(len(ious)), ious, color=JEV, width=0.7, zorder=2)
    ax.axhline(0.5, color=GRAY, ls="--", lw=1)
    ax.text(len(ious) - 0.5, 0.52, "IoU 0.5", ha="right", fontsize=8, color=INK2)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_xlabel("20 張整張圖（依 IoU 排序）")
    ax.set_ylabel("Box 與標註頭框 IoU")
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax.set_title(f"Box「未戴安全帽的人員頭部」平均 IoU {e4['box']['iou_mean']:.2f}", fontsize=10)
    _save(fig, "e4_ppe.png")


def fig_feed(e5: dict) -> None:
    imgs = sorted(e5["images"], key=lambda i: (i["synthetic"], sum(i["p_jam_runs"])))
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    for i, im in enumerate(imgs):
        p = sum(im["p_jam_runs"]) / len(im["p_jam_runs"])
        ax.bar(i, p, color=LLM if im["synthetic"] else JEV, width=0.7, zorder=2)
    ax.axhline(e5["threshold_p_jam"], color=INK2, ls="--", lw=1)
    ax.text(-0.4, e5["threshold_p_jam"] + 0.02, "門檻 p_jam 0.7", fontsize=8, color=INK2)
    ax.set_xticks(range(len(imgs)), [("合成:" + i["file"].replace("synth_jam_", "").replace(".jpg", "")) if i["synthetic"]
                                     else i["file"].replace(".jpg", "") for i in imgs], rotation=50, ha="right", fontsize=8)
    ax.set_ylim(0, 1)
    ax.set_ylabel("p_jam（3 次平均）")
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=JEV, label="公開正常照片（Wikimedia）"), Patch(color=LLM, label="合成卡料（影像編輯）")],
              fontsize=8, frameon=False, loc="upper left")
    ax.set_title("E5 V2 送料卡料零樣本判斷")
    _save(fig, "e5_feed.png")


def fig_e2e(e6: dict) -> None:
    segs5 = e6["feed_jam_interval5"]["segments_s"]
    segs2 = e6["feed_jam_interval2"]["segments_s"]
    ppe = e6["ppe_violation"]["segments_s"]
    rows = [("卡料（關鍵幀 5 秒）", [("畫面卡料→規則成立", segs5["seg_jamvisible_to_incident"]["p50"]),
                                ("規則→通知", segs5["seg_incident_to_notification"]["p50"]),
                                ("通知→LLM 說明", segs5["seg_notification_to_llm"]["p50"])]),
            ("卡料（關鍵幀 2 秒）", [("畫面卡料→規則成立", segs2["seg_jamvisible_to_incident"]["p50"]),
                                ("規則→通知", segs2["seg_incident_to_notification"]["p50"]),
                                ("通知→LLM 說明", segs2["seg_notification_to_llm"]["p50"])]),
            ("未戴安全帽", [("違規畫面→規則成立", ppe["seg_onset_to_incident"]["p50"]),
                          ("規則→通知", ppe["seg_incident_to_notification"]["p50"])])]
    cols = [JEV, RULE, LLM]
    fig, ax = plt.subplots(figsize=(8.0, 2.9))
    for i, (name, parts) in enumerate(rows):
        left = 0.0
        for j, (lab, v) in enumerate(parts):
            ax.barh(i, v, left=left, color=cols[j], height=0.5, edgecolor="white", linewidth=2, zorder=2,
                    label=lab.split("→", 1)[0].replace("畫面卡料", "異常").replace("違規畫面", "異常") + "→" + lab.split("→")[1] if i == 0 else None)
            if v > 0.6:
                ax.text(left + v / 2, i, f"{v:.1f}s", ha="center", va="center", color="white", fontsize=8, fontweight="bold")
            left += v
        ax.text(left + 0.15, i, f"合計 {left:.1f} s", va="center", fontsize=9, color=INK)
    ax.set_yticks(range(3), [r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlabel("秒（P50，各 5 次）")
    ax.set_xlim(0, 13)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.legend(fontsize=8, frameon=False, loc="lower right", ncol=3)
    ax.set_title("E6 端到端延遲分段")
    _save(fig, "e6_e2e_segments.png")


def fig_yolo(e7d: dict) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    labels = ["head（SH17）", "helmet（SH17）", "head（HardHat）", "helmet（HardHat）"]
    ours = [e7d["sh17_val"]["ours"]["head"]["mAP50"], e7d["sh17_val"]["ours"]["helmet"]["mAP50"],
            e7d["hardhat_val"]["ours"]["head"]["mAP50"], e7d["hardhat_val"]["ours"]["helmet"]["mAP50"]]
    base = [e7d["sh17_val"]["baseline_sh17_yolov8s"]["head"]["mAP50"], e7d["sh17_val"]["baseline_sh17_yolov8s"]["helmet"]["mAP50"], None, None]
    x = range(4)
    ax.bar([i - 0.18 for i in x], ours, 0.34, color=JEV, label="本專案 ppe.pt（SH17＋HardHat）", zorder=2)
    ax.bar([i + 0.18 for i in range(2)], base[:2], 0.34, color=GRAY, label="SH17 官方 YOLOv8s 基準", zorder=2)
    for i, v in enumerate(ours):
        ax.text(i - 0.18, v + 0.01, f"{v:.3f}", ha="center", fontsize=8)
    for i, v in enumerate(base[:2]):
        ax.text(i + 0.18, v + 0.01, f"{v:.3f}", ha="center", fontsize=8, color=INK2)
    ax.set_xticks(list(x), labels)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("mAP50")
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax.legend(fontsize=8, frameon=False, loc="lower left")
    ax.set_title("E7 PPE 偵測器驗證集 mAP50（訓練端回報）")
    _save(fig, "e7_yolo_map50.png")


def main() -> int:
    e2 = C.load_json("e2_rules_jev_llm.json")
    e1 = C.load_json("e1_root_cause_jev.json")
    e2["jev"]["chance"] = e1["chance_baseline"]["top1"]
    fig_comparison(e2)
    fig_latency(e2)
    fig_reliability(C.load_json("e3_calibration.json"))
    fig_ppe(C.load_json("e4_ppe_jev.json"))
    fig_feed(C.load_json("e5_feed_jev.json"))
    fig_e2e(C.load_json("e6_end_to_end.json"))
    fig_yolo(e7())
    print("figures →", C.FIGURES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
