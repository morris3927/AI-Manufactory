"""成果說明書建置：Markdown → Word（pandoc）＋ PDF（HTML 經 Chrome 列印）。

用法（專案根目錄）：
    prototype/backend/.venv/bin/python 03_成果說明書/build_report.py
需要：pandoc；PDF 另需 node＋playwright（使用本機 Chrome）。
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "成果說明書_v2.md"
OUT = HERE / "output"
NAME = "Sentinel_AI_第二階段成果說明書"

FIG = "figures"
IMAGES = {
    "圖 0-1": [(f"{FIG}/fig0-1_press_feed.png", "圖 0-1　沖床送料口示意圖：卡料位置、咬合點與危險區")],
    "圖 3-1": [(f"{FIG}/fig3-1_architecture.png", "圖 3-1　系統架構：規則（藍）、Jev（綠）、LLM（橘）、人（紫）；AI 只讀不控制")],
    "圖 4-1": [(f"{FIG}/fig4-1_timeline.png", "圖 4-1　人工流程與 Sentinel AI 的時間比較（中間情境，假設值）")],
    "圖 4-2～4-7": [
        (f"{FIG}/report/04_dashboard_alert.jpg", "圖 4-2　戰情儀表板：送料口畫面由 Jev 框出卡料位置，即時告警列出 S2 事件"),
        (f"{FIG}/report/05_kanban_assigned.jpg", "圖 4-3　任務看板：工單已派給當班技術員，顯示嚴重度與接單倒數"),
        (f"{FIG}/report/06_workorder_diagnosis.jpg", "圖 4-4　工單詳情：關鍵畫面、機台訊號、前三名原因與機率、DeepSeek 說明、引用的相似案例"),
        (f"{FIG}/report/07_permit_requested_leader_view.jpg", "圖 4-5　作業許可由班長核准後，才解鎖上鎖掛牌清單"),
        (f"{FIG}/report/09_agent_chat.jpg", "圖 4-6　維修助理：有風險的步驟被 Jev 安全把關標紅、要求主管確認；AI 不宣告可以復電"),
        (f"{FIG}/report/11b_writeback_approved.jpg", "圖 4-7　完工 Checklist、結案回報（Jev 檢查通過）與知識回寫「已核准入庫」"),
    ],
    "圖 4-8": [(f"{FIG}/report/10_mobile_technician.jpg", "圖 4-8　技術員手機畫面")],
    "圖 5-1": [(f"{FIG}/eval/e3_reliability.png", "圖 5-1　E3 校準曲線：Jev 說 0.8 以上時實際約 97% 正確；0.6–0.8 過度自信")],
    "圖 5-2～5-6": [
        (f"{FIG}/eval/e2_top1_comparison.png", "圖 5-2　E2 根因第一名命中率：純規則、Jev、DeepSeek（95% 信賴區間）"),
        (f"{FIG}/eval/e2_latency.png", "圖 5-3　E2 回應時間分布：Jev 穩定在 1 秒內，LLM 有長尾"),
        (f"{FIG}/eval/e4_ppe.png", "圖 5-4　E4 Jev 判斷安全帽（SH17 真實照片 120 張）"),
        (f"{FIG}/eval/e6_e2e_segments.png", "圖 5-5　E6 從異常到通知的各段時間"),
        (f"{FIG}/eval/e7_yolo_map50.png", "圖 5-6　E7 安全帽偵測模型微調前後"),
    ],
}
EXTRA_AFTER = {   # 標題後面補圖（原稿沒有佔位符的地方）
    "### 4.4 其他場景": [(f"{FIG}/report/14_dashboard_s3_report.jpg",
                          "圖 4-9　S3 人員進入危險區：主管標記傷亡後，儀表板出現職災 8 小時通報倒數與「不得移動現場」提醒")],
    "**E3：Jev 的機率準不準**": [(f"{FIG}/eval/e3_reliability.png", "圖 5-1　E3 校準曲線：改良檢索後，Jev 說 0.8 以上時實際約 97% 正確")],
    "**E5：Jev 看畫面判斷卡料**": [(f"{FIG}/eval/e5_feed.png", "E5　正常照片與合成卡料圖的卡料機率")],
    "### 6.3 導入成本與回本期": [(f"{FIG}/report/18_metrics.jpg", "圖 6-1　系統效益頁：反應時間、MTTR、AI 實際成本累計")],
}


WIDTH = {"07_permit": "62%", "09_agent": "62%", "11b_writeback": "62%", "10_mobile": "34%",
         "fig4-1": "92%", "e3_rel": "88%", "e2_latency": "80%", "e5_feed": "80%", "e7_yolo": "75%",
         "e6_e2e": "85%", "e2_top1": "70%", "e4_ppe": "80%"}


def images_md(items):
    out = []
    for path, cap in items:
        w = next((v for k, v in WIDTH.items() if k in path), "100%")
        out.append(f"![{cap}]({path}){{width={w}}}")
    return "\n\n".join(out)


def build_markdown() -> str:
    s = SRC.read_text(encoding="utf-8")
    def repl(m):
        key = m.group(1)
        for k, items in IMAGES.items():
            if key.startswith(k):
                return images_md(items)
        print("未對應的佔位符：", key, file=sys.stderr)
        return m.group(0)
    s = re.sub(r"【(圖 [^：】]+)[：】][^】]*】?", repl, s)
    for anchor, items in EXTRA_AFTER.items():
        idx = s.find(anchor)
        if idx < 0:
            print("找不到插圖位置：", anchor, file=sys.stderr)
            continue
        end = s.find("\n", idx)
        s = s[:end + 1] + "\n" + images_md(items) + "\n" + s[end + 1:]
    return s


CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: "PingFang TC", "Heiti TC", "Microsoft JhengHei", sans-serif; font-size: 10.5pt;
       line-height: 1.65; color: #1f2937; }
h1 { font-size: 20pt; margin: 0.3em 0; color: #111827; }
h2 { font-size: 15pt; border-bottom: 2px solid #2563eb; padding-bottom: 4px; margin-top: 1.6em;
     page-break-after: avoid; }
h3 { font-size: 12pt; color: #1e3a8a; margin-top: 1.2em; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #d1d5db; padding: 4px 6px; vertical-align: top; }
th { background: #eff6ff; }
blockquote { margin: 0.8em 0; padding: 0.5em 0.9em; background: #f8fafc; border-left: 4px solid #059669; }
figure { margin: 0.6em 0; text-align: center; page-break-inside: avoid; }
img { max-width: 100%; max-height: 150mm; }
figcaption { font-size: 9pt; color: #4b5563; margin-top: 3px; }
code { font-size: 9pt; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 1.2em 0; }
"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    md = build_markdown()
    build_md = HERE / "_build.md"
    build_md.write_text(md, encoding="utf-8")
    try:
        docx = OUT / f"{NAME}.docx"
        subprocess.run(["pandoc", str(build_md), "-o", str(docx), "--resource-path", str(HERE),
                        "-f", "markdown+pipe_tables", "--metadata", "lang=zh-TW"], check=True)
        print("Word：", docx)
        html = HERE / "_build.html"
        (HERE / "_build.css").write_text(CSS, encoding="utf-8")
        subprocess.run(["pandoc", str(build_md), "-s", "-o", str(html), "--resource-path", str(HERE),
                        "-c", "_build.css", "--metadata", f"pagetitle={NAME}", "--metadata", "lang=zh-TW"], check=True)
        pdf = OUT / f"{NAME}.pdf"
        node = shutil.which("node")
        pw = Path(sys.argv[1]) if len(sys.argv) > 1 else None
        if node and pw:
            js = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const b = await chromium.launch({{ channel: 'chrome' }});
  const p = await b.newPage();
  await p.goto('file://{html}', {{ waitUntil: 'networkidle' }});
  await p.pdf({{ path: '{pdf}', format: 'A4', printBackground: true,
    margin: {{ top: '18mm', bottom: '18mm', left: '16mm', right: '16mm' }},
    displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#6b7280">Sentinel AI｜第二階段成果說明書｜<span class="pageNumber"></span>／<span class="totalPages"></span></div>' }});
  await b.close();
}})();"""
            subprocess.run([node, "-e", js], check=True, cwd=pw)
            print("PDF：", pdf)
        else:
            print("略過 PDF（需要 node 與 playwright 目錄參數）")
    finally:
        for f in ("_build.md", "_build.html", "_build.css"):
            (HERE / f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
