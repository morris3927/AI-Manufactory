"""pandoc 轉出的 Word 再整理一次，讓它可以直接在 Word 裡修改、另存 PDF。

- 字型：中文微軟正黑體、英數 Calibri，內文 10.5 pt，行距 1.2 倍（表格內單行距）
- 標題：黑字，一、＝15 pt、1.＝12 pt、1.1＝10.5 pt 粗體
- 表格：細灰框線、表頭淡藍底、欄寬依內容自動調整
- 引用框：左側綠線、淡灰底
- 圖說：9 pt 灰字置中
- 版面：A4，上下 18 mm、左右 16 mm；第 1 頁是封面（不印頁碼），第 2 頁目錄起算第 1 頁，頁尾置中只有頁碼
- 目錄：封面後插入 TOC 功能變數（章、節兩層），由 Word 開檔後更新（見 build_report.py）

用法：python polish_docx.py <檔案.docx>（直接覆寫）
"""
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

FONT_EA = "Microsoft JhengHei"
FONT_LATIN = "Calibri"
BORDER = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'


def rfonts() -> str:
    return f'<w:rFonts w:ascii="{FONT_LATIN}" w:hAnsi="{FONT_LATIN}" w:eastAsia="{FONT_EA}" w:cs="{FONT_LATIN}"/>'


def set_style(styles: str, sid: str, ppr: str | None, rpr: str | None) -> str:
    """把指定樣式的 pPr／rPr 換成新的（沒有就加上）。"""
    m = re.search(r'(<w:style [^>]*w:styleId="%s"[^>]*>)(.*?)(</w:style>)' % sid, styles, re.S)
    if not m:
        return styles
    body = m.group(2)
    if ppr is not None:
        body = re.sub(r"<w:pPr>.*?</w:pPr>|<w:pPr\s*/>", "", body, flags=re.S) + f"<w:pPr>{ppr}</w:pPr>"
    if rpr is not None:
        body = re.sub(r"<w:rPr>.*?</w:rPr>|<w:rPr\s*/>", "", body, flags=re.S) + f"<w:rPr>{rpr}</w:rPr>"
    # pPr 必須在 rPr 前面（schema 順序）
    ppr_m = re.search(r"<w:pPr>.*?</w:pPr>", body, re.S)
    rpr_m = re.search(r"<w:rPr>.*?</w:rPr>", body, re.S)
    if ppr_m and rpr_m and ppr_m.start() > rpr_m.start():
        body = body.replace(ppr_m.group(0), "").replace(rpr_m.group(0), ppr_m.group(0) + rpr_m.group(0))
    return styles[: m.start()] + m.group(1) + body + m.group(3) + styles[m.end():]


def heading(size_half_pt: int, before: int, after: int, level: int) -> tuple[str, str]:
    ppr = (f'<w:keepNext/><w:keepLines/><w:spacing w:before="{before}" w:after="{after}"/>'
           f'<w:outlineLvl w:val="{level}"/>')
    if level == 1:   # 一、大標下面一條細灰線
        ppr = ppr.replace("<w:spacing", '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="2" w:color="9CA3AF"/></w:pBdr><w:spacing')
    rpr = f'{rfonts()}<w:b/><w:color w:val="000000"/><w:sz w:val="{size_half_pt}"/><w:szCs w:val="{size_half_pt}"/>'
    return ppr, rpr


def polish_styles(s: str) -> str:
    s = re.sub(r"<w:docDefaults>.*?</w:docDefaults>", (
        "<w:docDefaults><w:rPrDefault><w:rPr>" + rfonts() +
        '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US" w:eastAsia="zh-TW" w:bidi="ar-SA"/>'
        "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>"
        '<w:spacing w:after="120" w:line="288" w:lineRule="auto"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"), s, flags=re.S)
    s = set_style(s, "BodyText", '<w:spacing w:before="60" w:after="120"/>', None)
    s = set_style(s, "FirstParagraph", '<w:spacing w:before="60" w:after="120"/>', None)
    s = set_style(s, "Compact", '<w:spacing w:before="20" w:after="20"/>', None)
    for sid, args in {"Heading1": (40, 120, 120, 0), "Heading2": (30, 360, 120, 1),
                      "Heading3": (24, 240, 80, 2), "Heading4": (21, 180, 60, 3)}.items():
        s = set_style(s, sid, *heading(*args))
    s = set_style(s, "Title", '<w:spacing w:before="240" w:after="160"/><w:jc w:val="left"/>',
                  f'{rfonts()}<w:b/><w:color w:val="000000"/><w:sz w:val="40"/><w:szCs w:val="40"/>')
    s = set_style(s, "TOCHeading", '<w:spacing w:before="0" w:after="240"/><w:outlineLvl w:val="9"/>',
                  f'{rfonts()}<w:b/><w:color w:val="000000"/><w:sz w:val="32"/><w:szCs w:val="32"/>')
    s = set_style(s, "Caption", '<w:spacing w:before="40" w:after="200"/><w:jc w:val="center"/>',
                  f'{rfonts()}<w:color w:val="4B5563"/><w:sz w:val="18"/><w:szCs w:val="18"/>')
    s = set_style(s, "ImageCaption", '<w:spacing w:before="40" w:after="200"/><w:jc w:val="center"/>',
                  f'{rfonts()}<w:color w:val="4B5563"/><w:sz w:val="18"/><w:szCs w:val="18"/>')
    s = set_style(s, "CaptionedFigure", '<w:keepNext/><w:spacing w:before="120" w:after="0"/><w:jc w:val="center"/>', None)
    s = set_style(s, "Figure", '<w:keepNext/><w:spacing w:before="120" w:after="0"/><w:jc w:val="center"/>', None)
    s = set_style(s, "BlockText", (
        '<w:pBdr><w:left w:val="single" w:sz="24" w:space="8" w:color="059669"/></w:pBdr>'
        '<w:shd w:val="clear" w:color="auto" w:fill="F3F4F6"/>'
        '<w:spacing w:before="80" w:after="80"/><w:ind w:left="284" w:right="0" w:firstLine="0"/>'), None)
    # 表格：細灰框線、表頭淡藍底粗體
    s = re.sub(r'(<w:style [^>]*w:styleId="Table"[^>]*>.*?<w:tblPr>)(.*?)(</w:tblPr>)',
               lambda m: m.group(1) + '<w:tblInd w:w="0" w:type="dxa"/><w:tblBorders>' +
               "".join(BORDER.format(k) for k in ("top", "left", "bottom", "right", "insideH", "insideV")) +
               '</w:tblBorders><w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
               '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar>' + m.group(3) +
               '<w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
               '<w:rPr><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr>',
               s, count=1, flags=re.S)
    s = re.sub(r'(<w:style [^>]*w:styleId="Table"[^>]*>.*?)<w:tblStylePr w:type="firstRow">.*?</w:tblStylePr>',
               lambda m: m.group(1) + '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr>'
               '<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="EFF6FF"/></w:tcPr></w:tblStylePr>',
               s, count=1, flags=re.S)
    return s


FOOTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
          '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
          '<w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
          '<w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
          '<w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
          '<w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:t>1</w:t></w:r>'
          '<w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>'
          "</w:p></w:ftr>")
SECT = ('<w:sectPr><w:footerReference w:type="default" r:id="rIdSentinelFooter"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1020" w:right="907" w:bottom="1020" w:left="907" w:header="567" w:footer="454" w:gutter="0"/>'
        '<w:pgNumType w:start="0"/><w:titlePg/>'          # 封面是第 0 頁且不印頁碼 → 目錄頁為第 1 頁
        "</w:sectPr>")
PAGE_BREAK = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
TOC = ('<w:p><w:pPr><w:pStyle w:val="TOCHeading"/></w:pPr><w:r><w:t>目錄</w:t></w:r></w:p>'
       '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
       '<w:r><w:instrText xml:space="preserve"> TOC \\o "2-3" \\h \\z \\u </w:instrText></w:r>'
       '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
       '<w:r><w:t>（目錄：在 Word 裡按右鍵「更新功能變數」即可產生）</w:t></w:r>'
       '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')


def polish_document(d: str) -> str:
    d = re.sub(r"<w:sectPr\b.*?</w:sectPr>|<w:sectPr\s*/>", "", d, flags=re.S)
    # 封面兩行大標（md 的 #）改成 Title，不進目錄
    for _ in range(2):
        d = d.replace('<w:pStyle w:val="Heading1" />', '<w:pStyle w:val="Title" />', 1)
    # 封面之後：換頁 → 目錄 → 換頁，再接第一個章標題
    m = re.search(r'<w:p>(?:(?!</w:p>).)*?<w:pStyle w:val="Heading2" />', d, re.S)
    if m:
        d = d[:m.start()] + PAGE_BREAK + TOC + PAGE_BREAK + d[m.start():]
    d = d.replace("</w:body>", SECT + "</w:body>")
    d = d.replace('<w:tblLayout w:type="fixed" />', '<w:tblLayout w:type="autofit" />')
    if 'xmlns:r="' not in d[:2000]:
        d = d.replace("<w:document ", '<w:document xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
    return d


def main(path: Path) -> None:
    tmp = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(path) as z:
        z.extractall(tmp)
    (tmp / "word/styles.xml").write_text(polish_styles((tmp / "word/styles.xml").read_text("utf-8")), "utf-8")
    (tmp / "word/document.xml").write_text(polish_document((tmp / "word/document.xml").read_text("utf-8")), "utf-8")
    (tmp / "word/footer_sentinel.xml").write_text(FOOTER, "utf-8")
    rels = tmp / "word/_rels/document.xml.rels"
    r = rels.read_text("utf-8")
    if "rIdSentinelFooter" not in r:
        r = r.replace("</Relationships>", '<Relationship Id="rIdSentinelFooter" '
                      'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
                      'Target="footer_sentinel.xml"/></Relationships>')
        rels.write_text(r, "utf-8")
    ct = tmp / "[Content_Types].xml"
    c = ct.read_text("utf-8")
    if "footer_sentinel.xml" not in c:
        c = c.replace("</Types>", '<Override PartName="/word/footer_sentinel.xml" '
                      'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>')
        ct.write_text(c, "utf-8")
    out = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(tmp.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(tmp).as_posix())
    shutil.move(out, path)
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
