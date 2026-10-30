"""用 Microsoft Word（macOS）開啟成果說明書：更新目錄與頁碼後存回 .docx，並由 Word 另存 PDF。

這樣 Word 檔與 PDF 的排版完全一致，目錄頁碼也是 Word 算出來的。
Word 是沙盒程式，只能存取自己的容器資料夾，所以先複製過去處理，再複製回 output/。

用法：python word_finalize.py <檔案.docx>   （PDF 輸出到同一資料夾、同檔名）
"""
import shutil
import subprocess
import sys
from pathlib import Path

BOX = Path.home() / "Library/Containers/com.microsoft.Word/Data/Documents"

SCRIPT = """
tell application "Microsoft Word"
  open POSIX file "{docx}"
  delay 3
  set d to document 1
  update table of contents 1 of d
  save d
  save as d file name "{pdf_name}" file format format PDF
  close d saving no
end tell
"""


def main(src: Path) -> None:
    if not Path("/Applications/Microsoft Word.app").exists():
        print("略過：沒有安裝 Microsoft Word")
        return
    BOX.mkdir(parents=True, exist_ok=True)
    work = BOX / "sentinel_report.docx"
    pdf = BOX / "sentinel_report.pdf"
    shutil.copy(src, work)
    pdf.unlink(missing_ok=True)
    subprocess.run(["osascript", "-e", SCRIPT.format(docx=work, pdf_name=pdf.name)], check=True)
    shutil.copy(work, src)
    shutil.copy(pdf, src.with_suffix(".pdf"))
    print("Word 更新目錄並輸出 PDF：", src.with_suffix(".pdf"))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
