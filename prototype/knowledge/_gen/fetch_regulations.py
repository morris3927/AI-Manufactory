"""Fetch Taiwan regulation articles (law.moj.gov.tw) and OSHA 1910.147 paragraphs (eCFR)
and write knowledge/public/regulations.jsonl.

Text is taken verbatim from the official pages (no LLM involvement). Legal texts are not
copyright subject matter (Taiwan Copyright Act Art. 9; 17 U.S.C. §105 for US federal regs).

Uses system `curl` (macOS keychain) because python3.13's OpenSSL rejects the TWCA chain
of law.moj.gov.tw ("Missing Subject Key Identifier"). osha.gov blocks scripted access
(CloudFront 403), so OSHA text is taken from the official eCFR (ecfr.gov), which is the
authoritative codification of 29 CFR.

Usage: python3.13 fetch_regulations.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import subprocess
from pathlib import Path

KB = Path(__file__).resolve().parents[1]
OUT = KB / "public" / "regulations.jsonl"
CACHE = Path(__file__).resolve().parent / ".cache"
UA = "Mozilla/5.0 (SentinelAI-KB research prototype)"
TODAY = "2026-10-20"

# (pcode, law_name, articles, line, failure_mode_ids per article)
DIE_MODES = ["die_misalignment", "shut_height_misset", "counterbalance_misset", "punch_edge_wear",
             "stripper_spring_fatigue"]
TW_LAWS = [
    ("N0060009", "職業安全衛生設施規則", {
        "57": ("stamping", []), "58": ("stamping", []), "69": ("stamping", DIE_MODES),
        "70": ("stamping", DIE_MODES), "254": ("all", []), "277": ("all", []),
        "284": ("welding", []), "292": ("welding", []), "300": ("stamping", []),
    }),
    ("N0060027", "職業安全衛生管理辦法", {
        "26": ("stamping", ["clutch_brake_wear", "air_pressure_low"]),
        "59": ("stamping", ["clutch_brake_wear", "light_curtain_fault"]),
        "80": ("all", []),
    }),
    ("N0060034", "機械設備器具安全標準", {
        "3": ("stamping", []), "4": ("stamping", ["light_curtain_fault"]),
        "6": ("stamping", ["light_curtain_fault"]), "11-1": ("stamping", ["light_curtain_fault"]),
        "12": ("stamping", ["light_curtain_fault"]), "12-2": ("stamping", ["light_curtain_fault"]),
        "18-1": ("stamping", ["clutch_brake_wear"]), "19": ("stamping", ["clutch_brake_wear"]),
        "20": ("stamping", []), "22": ("stamping", DIE_MODES), "25": ("stamping", []),
        "31": ("stamping", ["clutch_brake_wear", "air_pressure_low"]),
        "35": ("stamping", ["clutch_brake_wear"]), "37": ("stamping", ["clutch_brake_wear"]),
        "38": ("stamping", ["clutch_brake_wear"]), "39": ("stamping", ["clutch_brake_wear"]),
        "40": ("stamping", ["air_pressure_low", "clutch_brake_wear"]),
        "42": ("stamping", ["counterbalance_misset", "air_pressure_low"]),
    }),
    ("N0060001", "職業安全衛生法", {"6": ("all", []), "18": ("all", []), "37": ("all", [])}),
]
# Previous (currently in-force until 2026-12-31) version of 設施規則 §57
OLD_57 = ("N0060009", "20240801")

OSHA_PARAS = ["(c)(4)(ii)", "(d)(1)", "(d)(2)", "(d)(3)", "(d)(4)", "(d)(5)", "(d)(6)",
              "(e)(1)", "(e)(2)", "(e)(3)", "(f)(1)", "(f)(3)"]
ECFR_URL = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-29?part=1910&section=1910.147"
ECFR_PUBLIC = "https://www.ecfr.gov/current/title-29/subtitle-B/chapter-XVII/part-1910/subpart-J/section-1910.147"


def curl(url: str, name: str) -> str:
    CACHE.mkdir(exist_ok=True)
    p = CACHE / name
    if not p.exists():
        subprocess.run(["curl", "-sS", "-L", "-A", UA, "--max-time", "60", "-o", str(p), url], check=True)
    return p.read_text(encoding="utf-8", errors="replace")


def roc_to_iso(s: str) -> str:
    m = re.search(r"民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if not m:
        return ""
    y, mo, d = (int(x) for x in m.groups())
    return dt.date(y + 1911, mo, d).isoformat()


def clean(fragment: str) -> str:
    t = re.sub(r"<div[^>]*>", "\n", fragment)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\r　]+", "", t)
    t = re.sub(r"\n+", "\n", t).strip()
    return t


def parse_articles(page: str) -> dict[str, str]:
    arts = {}
    pat = r'<div class="col-no">(.*?)</div>\s*<div class="col-data">\s*<div class="law-article">(.*?)</div>\s*</div>'
    for m in re.finditer(pat, page, re.S):
        no = re.sub(r"<[^>]+>", "", m.group(1))
        mm = re.search(r"第\s*([\d\-]+)\s*條", no)
        if mm:
            arts[mm.group(1)] = clean(m.group(2))
    return arts


def amended_date(page: str) -> str:
    m = re.search(r"修正日期：.*?(民國[^<]*日)", page, re.S)
    return roc_to_iso(m.group(1)) if m else ""


def split_long(text: str, limit: int = 400) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


def tw_chunks() -> list[dict]:
    out = []
    for pcode, law, wanted in TW_LAWS:
        page = curl(f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}", f"{pcode}.html")
        arts = parse_articles(page)
        amend = amended_date(page)
        for no, (line, fms) in wanted.items():
            if no not in arts:
                print(f"🔬 missing {law} 第{no}條 — left out")
                continue
            text = re.sub(r"^本條文有附件\n?", "", arts[no])
            eff, status, basis = amend, "in_force", f"現行版本（{amend} 修正）"
            if pcode == "N0060009":
                if no == "57":
                    eff, status = "2027-01-01", "promulgated_not_yet_effective"
                    basis = "115-06-30 修正，自 116-01-01 施行（本知識庫建置日 2026-10-20 時尚未施行；現行條文見 REG-N0060009-57-v20240801）"
                else:
                    eff = "2026-07-01"
                    basis = "115-06-30 修正版本；除第 21-3、57、116、185-1～185-4 條外，自 115-07-01 施行（個別條文是否於該次修正變動未逐條查證）"
            pieces = split_long(text)
            for i, piece in enumerate(pieces):
                cid = f"REG-{pcode}-{no}" + (f"-p{i + 1}" if len(pieces) > 1 else "")
                out.append(_tw_chunk(cid, pcode, law, no, piece, line, fms, amend, eff, status, basis,
                                     f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={no}",
                                     part=(i + 1, len(pieces))))
    # previous version of §57 (in force until 2026-12-31)
    pcode, ver = OLD_57
    page = curl(f"https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode={pcode}&lnndate={ver}&lser=001",
                f"{pcode}-{ver}.html")
    arts = parse_articles(page)
    if "57" in arts:
        out.append(_tw_chunk(f"REG-{pcode}-57-v{ver}", pcode, "職業安全衛生設施規則", "57", arts["57"],
                             "stamping", [], "2024-08-01", "2024-08-01", "in_force_until_2026-12-31",
                             "113-08-01 修正版本；116-01-01 起由 115-06-30 修正條文取代",
                             f"https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode={pcode}&lnndate={ver}&lser=001"))
    else:
        print("🔬 old §57 not found — left out")
    return out


def _tw_chunk(cid, pcode, law, no, text, line, fms, amend, eff, status, basis, url, part=(1, 1)):
    head = f"{law} 第 {no} 條" + (f"（第 {part[0]}／{part[1]} 段）" if part[1] > 1 else "")
    return {
        "chunk_id": cid, "doc_id": f"{pcode}-{no}", "doc_type": "regulation", "line": line,
        "machine_type": [], "failure_mode_ids": fms,
        "source": url, "license": "TW-law", "data_label": "公開資料", "lang": "zh-TW",
        "version": amend, "review_status": "approved",
        "law_name": law, "pcode": pcode, "article": f"第{no}條", "effective_date": eff,
        "effective_status": status, "effective_basis": basis,
        "title": head, "text": f"{head}\n{text}",
        "retrieved_at": TODAY, "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
        "license_note": "法令依著作權法第 9 條非著作權標的；來源：全國法規資料庫",
    }


def osha_chunks() -> list[dict]:
    page = curl(ECFR_URL, "ecfr-1910.147.html")
    out = []
    for para in OSHA_PARAS:
        key = "1910.147" + para
        texts = []
        for m in re.finditer(r'<p[^>]*data-title="([^"]+)"[^>]*>(.*?)</p>', page, re.S):
            dtitle = m.group(1)
            if dtitle == key or dtitle.startswith(key + "("):
                t = html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))
                texts.append(re.sub(r"\s+", " ", t).strip())
        if not texts:
            print(f"🔬 missing 1910.147{para} — left out")
            continue
        body = "\n".join(texts)
        words = body.split()
        if len(words) > 450:  # keep chunk size sane; note truncation explicitly
            body = " ".join(words[:450]) + " […continued in source]"
        cid = "REG-OSHA-1910.147" + re.sub(r"[()]", lambda x: "-" if x.group() == "(" else "", para)
        out.append({
            "chunk_id": cid, "doc_id": "OSHA-1910.147", "doc_type": "regulation", "line": "all",
            "machine_type": [], "failure_mode_ids": [],
            "source": ECFR_PUBLIC, "license": "PD", "data_label": "公開資料", "lang": "en",
            "version": "eCFR current", "review_status": "approved",
            "law_name": "29 CFR 1910.147 The control of hazardous energy (lockout/tagout)",
            "article": f"1910.147{para}", "effective_date": "current (eCFR, retrieved 2026-10-20)",
            "effective_status": "in_force (US)", "title": f"29 CFR 1910.147{para}",
            "text": f"29 CFR 1910.147{para}\n{body}", "retrieved_at": TODAY,
            "sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
            "license_note": "US federal regulation, public domain (17 U.S.C. §105); text from official eCFR because osha.gov blocked scripted access",
        })
    return out


def main():
    chunks = tw_chunks() + osha_chunks()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"wrote {len(chunks)} chunks -> {OUT}")


if __name__ == "__main__":
    main()
