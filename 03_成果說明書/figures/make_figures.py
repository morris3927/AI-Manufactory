"""成果說明書插圖：沖床送料示意圖、系統架構圖、人工 vs 系統時間軸。

用法：prototype/backend/.venv/bin/python 03_成果說明書/figures/make_figures.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

OUT = Path(__file__).resolve().parent
for f in ("/System/Library/Fonts/STHeiti Medium.ttc", "/Library/Fonts/Arial Unicode.ttf"):
    if Path(f).exists():
        font_manager.fontManager.addfont(f)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

INK = "#1f2937"
MUTED = "#6b7280"
RULE = "#2563eb"     # 規則
JEV = "#059669"      # Jev
LLM = "#d97706"      # LLM
HUMAN = "#7c3aed"    # 人
DANGER = "#dc2626"
PANEL = "#f3f4f6"


def box(ax, x, y, w, h, text, color, fc=None, fs=11, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                ec=color, fc=fc or "white", lw=1.6))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK,
            fontweight="bold" if bold else "normal", linespacing=1.35)


def arrow(ax, x1, y1, x2, y2, color=MUTED, style="-|>", lw=1.6, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=14,
                                 color=color, lw=lw, linestyle=ls))


def fig_press():
    fig, ax = plt.subplots(figsize=(11.8, 4.6))
    ax.set_xlim(0, 11.7); ax.set_ylim(0, 4.6); ax.axis("off")
    # 料捲
    ax.add_patch(Circle((1.0, 2.0), 0.75, fc="#d1d5db", ec=INK, lw=1.5))
    ax.add_patch(Circle((1.0, 2.0), 0.25, fc="white", ec=INK, lw=1.2))
    ax.text(1.0, 0.85, "① 料捲\n（一整捲鐵板）", ha="center", va="top", fontsize=10.5, color=INK)
    # 鐵板帶
    ax.plot([1.75, 7.2], [2.0, 2.0], color="#9ca3af", lw=5, solid_capstyle="butt")
    # 整平機
    for x in (2.6, 3.0, 3.4):
        ax.add_patch(Circle((x, 2.25), 0.14, fc="white", ec=INK)); ax.add_patch(Circle((x + 0.2, 1.75), 0.14, fc="white", ec=INK))
    ax.text(3.1, 0.85, "② 整平機\n（把鐵板壓平）", ha="center", va="top", fontsize=10.5, color=INK)
    # 送料滾輪
    ax.add_patch(Circle((5.0, 2.32), 0.3, fc="#e5e7eb", ec=INK, lw=1.4))
    ax.add_patch(Circle((5.0, 1.68), 0.3, fc="#e5e7eb", ec=INK, lw=1.4))
    ax.text(5.0, 0.85, "③ 送料滾輪\n（一次送一段）", ha="center", va="top", fontsize=10.5, color=INK)
    ax.annotate("咬合點\n（手會被捲入）", xy=(5.25, 2.0), xytext=(5.9, 3.3), fontsize=10, color=DANGER,
                ha="center", arrowprops=dict(arrowstyle="-|>", color=DANGER, lw=1.3))
    # 沖床與模具
    ax.add_patch(Rectangle((7.2, 3.0), 2.6, 0.9, fc="#e5e7eb", ec=INK, lw=1.5))
    ax.text(8.5, 3.45, "④ 沖床滑塊（往下壓）", ha="center", va="center", fontsize=10.5, color=INK)
    ax.add_patch(Rectangle((7.4, 2.35), 2.2, 0.35, fc="#cbd5e1", ec=INK))
    ax.add_patch(Rectangle((7.4, 1.3), 2.2, 0.55, fc="#cbd5e1", ec=INK))
    ax.text(8.5, 1.05, "⑤ 模具（壓出機殼零件）", ha="center", va="top", fontsize=10.5, color=INK)
    arrow(ax, 8.5, 2.95, 8.5, 2.72, color=INK)
    # 卡料
    ax.add_patch(Polygon([[6.75, 1.95], [7.55, 2.45], [7.6, 2.25], [6.85, 1.85]], fc="#f59e0b", ec=INK, lw=1.2))
    ax.add_patch(Polygon([[6.9, 2.05], [7.5, 2.55], [7.55, 2.38], [6.98, 1.98]], fc="#fbbf24", ec=INK, lw=1.0))
    ax.annotate("卡料：鐵板翹起、疊在模具入口", xy=(7.1, 2.25), xytext=(3.2, 3.75), fontsize=10.5, color=DANGER,
                ha="center", fontweight="bold", arrowprops=dict(arrowstyle="-|>", color=DANGER, lw=1.3))
    # 危險區
    ax.add_patch(Rectangle((4.55, 1.2), 5.4, 2.85, fc="none", ec=DANGER, lw=1.4, ls="--"))
    ax.text(9.93, 4.12, "危險區：維修前必須停機、上鎖掛牌", ha="right", va="bottom", fontsize=10, color=DANGER)
    # 攝影機
    ax.text(10.75, 2.0, "攝影機看送料口\n＋\n機台數據\n（送料速率、誤差）", ha="center", va="center", fontsize=10, color=JEV)
    fig.savefig(OUT / "fig0-1_press_feed.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_architecture():
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_xlim(0, 12); ax.set_ylim(0, 7); ax.axis("off")
    # 資料來源
    ax.text(0.3, 6.6, "資料來源（原型中為模擬）", fontsize=11, color=MUTED)
    for i, t in enumerate(["攝影機影像", "機台數據（MES Log）", "班表、門禁"]):
        box(ax, 0.3, 5.3 - i * 1.0, 2.2, 0.75, t, MUTED, fc=PANEL)
    # 感測
    box(ax, 3.1, 5.3, 2.3, 0.75, "YOLO：人頭／安全帽", RULE)
    box(ax, 3.1, 4.3, 2.3, 0.75, "數據門檻判定", RULE)
    box(ax, 3.1, 2.35, 2.3, 0.75, "Jev 看關鍵畫面\n（卡料、安全帽確認）", JEV, fs=10)
    for y in (5.68, 4.68):
        arrow(ax, 2.5, y, 3.1, y)
    arrow(ax, 2.5, 5.45, 3.1, 2.9)
    # 規則
    box(ax, 6.0, 3.9, 2.4, 1.9, "規則引擎\n\n雙重確認、嚴重度\n分流、派給對的人\n逐級升級", RULE, fs=10.5, bold=False)
    for y in (5.68, 4.68):
        arrow(ax, 5.4, y, 6.0, y)
    arrow(ax, 5.4, 2.73, 6.0, 4.05)
    arrow(ax, 2.5, 3.68, 6.0, 3.95, ls="--")
    ax.text(4.25, 3.95, "班表決定派給誰", fontsize=9, color=MUTED, ha="center")
    # Jev
    box(ax, 6.0, 2.2, 2.4, 1.2, "Jev 快速判斷（約 0.5 秒）\n最可能原因＋機率\n嚴重度只升不降", JEV, fs=10)
    arrow(ax, 7.2, 3.9, 7.2, 3.4, color=JEV)
    # 派工
    box(ax, 9.0, 4.4, 2.7, 1.4, "派工通知（< 1 秒）\n對的人＋前三名原因\n＋SOP 編號", HUMAN, fs=10.5)
    arrow(ax, 8.4, 4.85, 9.0, 5.0, color=HUMAN)
    arrow(ax, 8.4, 2.8, 9.0, 4.6, color=JEV)
    # LLM
    box(ax, 9.0, 2.2, 2.7, 1.6, "LLM 維修助理（數秒）\n說明、一問一答\n作業許可→上鎖掛牌\n→才給維修步驟", LLM, fs=10)
    arrow(ax, 8.4, 2.6, 9.0, 2.6, color=LLM, ls="--")
    ax.text(8.7, 2.05, "非同步：不拖慢派工", fontsize=9, color=LLM, ha="center")
    # 閉環
    box(ax, 6.0, 0.4, 5.7, 1.1, "結案：Checklist → 回報 → Jev 檢查完整\n→ 主管核准 → 寫回知識庫（下次找得到這筆經驗）", INK, fc=PANEL, fs=10.5)
    arrow(ax, 10.35, 2.2, 10.35, 1.5, color=INK)
    box(ax, 3.1, 0.4, 2.3, 1.1, "知識庫\nSOP、案例、法規", INK, fc=PANEL, fs=10.5)
    arrow(ax, 6.0, 0.95, 5.4, 0.95, color=INK)
    arrow(ax, 4.25, 1.5, 6.0, 2.5, color=JEV, ls="--")
    # 紅線
    ax.plot([0.2, 11.8], [6.25, 6.25], color=DANGER, lw=1.6, ls="--")
    ax.text(11.8, 6.32, "AI 只讀、不控制：沒有任何寫入機台或安全迴路的介面", ha="right", va="bottom", fontsize=10.5, color=DANGER)
    # 圖例
    for i, (c, t) in enumerate([(RULE, "規則（毫秒）"), (JEV, "Jev（約 0.5 秒）"), (LLM, "LLM（數秒）"), (HUMAN, "人")]):
        ax.add_patch(Rectangle((3.1 + i * 2.0, 6.55), 0.25, 0.22, fc=c))
        ax.text(3.42 + i * 2.0, 6.66, t, fontsize=10, va="center", color=INK)
    fig.savefig(OUT / "fig3-1_architecture.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_timeline():
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.set_xlim(-7, 40); ax.set_ylim(0, 3.2); ax.axis("off")

    def seg(y, x0, x1, color, label):
        ax.add_patch(Rectangle((x0, y), x1 - x0, 0.55, fc=color, ec="white", lw=1.5))
        if x1 - x0 > 2:
            ax.text((x0 + x1) / 2, y + 0.27, label, ha="center", va="center", fontsize=10, color="white")

    ax.text(-1.0, 2.57, "人工", fontsize=12, color=INK, va="center", ha="right")
    seg(2.3, 0, 5, MUTED, "找人")
    seg(2.3, 5, 35, "#9ca3af", "到場後猜原因、試錯、修理（約 30 分）")
    ax.text(-1.0, 1.22, "Sentinel AI", fontsize=12, color=INK, va="center", ha="right")
    seg(0.95, 0, 0.5, HUMAN, "")
    seg(0.95, 0.5, 23, JEV, "帶著前三名原因到場、照 SOP 一步步修（MTTR −25%，假設）")
    ax.annotate("確認後 < 1 秒派給當班技術員", xy=(0.25, 1.5), xytext=(4, 1.85), fontsize=10, color=HUMAN,
                arrowprops=dict(arrowstyle="-|>", color=HUMAN))
    ax.add_patch(Rectangle((23, 0.95), 12, 0.55, fc="none", ec=JEV, lw=1.5, ls="--"))
    ax.text(29, 1.22, "省下的時間", ha="center", va="center", fontsize=11, color=JEV, fontweight="bold")
    for m in range(0, 40, 5):
        ax.text(m, 0.6, f"{m}", ha="center", fontsize=9, color=MUTED)
    ax.text(39.5, 0.6, "分鐘", ha="right", fontsize=9, color=MUTED)
    ax.text(0, 0.15, "中間情境：找人延遲 5 分鐘、修理 30 分鐘、MTTR 降 25%（均為假設，見 6.2 節）", fontsize=9.5, color=MUTED)
    fig.savefig(OUT / "fig4-1_timeline.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    fig_press(); fig_architecture(); fig_timeline()
    print("ok", sorted(p.name for p in OUT.glob("*.png")))
