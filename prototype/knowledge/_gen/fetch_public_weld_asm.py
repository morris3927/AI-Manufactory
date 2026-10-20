"""Fetch public (non-copyright / openly reusable) texts for the welding and assembly lines.

Outputs (verbatim text, no LLM):
  public/regulations_weld_asm.jsonl — Taiwan regulations (law.moj.gov.tw) + 29 CFR 1910 Subpart Q
                                       and 1910.147(a)(2)(iii)(A) (eCFR)
  public/guidance_weld_asm.jsonl    — OSHA FS-3647 (PD), NASA-STD-8739.4A Change 4 (PD, NASA),
                                       HSE INDG316 (Crown copyright, freely reproducible except
                                       advertising/endorsement/commercial; acknowledge HSE)

Skipped and recorded in README: NIOSH 2011-156 (cdc.gov returns HTTP 403 to scripted access).
Reuses the stamping fetcher helpers (fetch_regulations.py is NOT modified).

Usage: python3.13 fetch_public_weld_asm.py
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess

from fetch_regulations import CACHE, KB, TODAY, _tw_chunk, amended_date, curl, parse_articles, split_long

OUT_REG = KB / "public" / "regulations_weld_asm.jsonl"
OUT_GUIDE = KB / "public" / "guidance_weld_asm.jsonl"

TW_LAWS = [
    ("N0060009", "職業安全衛生設施規則", {
        "106": ("welding", ["shielding_gas_low"]), "108": ("welding", ["shielding_gas_low"]),
        "173": ("welding", []), "174": ("welding", []),
        "245": ("welding", ["ground_poor"]), "250": ("welding", ["ground_poor"]),
        "277-1": ("welding", ["fume_extraction_fault"]), "287": ("welding", ["fume_extraction_fault"]),
        "324-1": ("assembly", []),
    }),
    ("N0060025", "工業用機器人危害預防標準", {
        "4": ("welding", []), "13": ("welding", []), "21": ("welding", []), "24": ("welding", []),
        "25": ("welding", []), "26": ("welding", []), "27": ("welding", ["tcp_drift"]),
        "28": ("welding", ["nozzle_spatter_buildup"]), "29": ("welding", []), "31": ("welding", []),
        "32": ("welding", []), "33": ("welding", []),
    }),
    ("N0060027", "職業安全衛生管理辦法", {
        "40": ("welding", ["fume_extraction_fault"]), "47": ("welding", ["fume_extraction_fault"]),
        "60": ("welding", []), "66": ("welding", ["tcp_drift"]), "77": ("assembly", []),
    }),
]

ECFR = "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-29?part=1910&section={sec}"
ECFR_PUBLIC = "https://www.ecfr.gov/current/title-29/subtitle-B/chapter-XVII/part-1910/subpart-{sub}/section-{sec}"
ECFR_PARAS = [  # (section, subpart, paragraph, line, failure_mode_ids, include_children)
    ("1910.147", "J", "(a)(2)(iii)(A)", "assembly", [], False),
    ("1910.252", "Q", "(a)(1)", "welding", [], True),
    ("1910.252", "Q", "(a)(2)(ii)", "welding", [], True),
    ("1910.252", "Q", "(a)(2)(iii)", "welding", [], True),
    ("1910.252", "Q", "(a)(2)(iv)", "welding", [], True),
    ("1910.252", "Q", "(c)(1)(iii)", "welding", ["fume_extraction_fault"], True),
    ("1910.252", "Q", "(c)(3)", "welding", ["fume_extraction_fault"], True),
    ("1910.254", "Q", "(c)(2)", "welding", ["ground_poor"], True),
    ("1910.254", "Q", "(d)(2)", "welding", ["ground_poor"], True),
    ("1910.254", "Q", "(d)(3)", "welding", ["ground_poor"], True),
    ("1910.254", "Q", "(d)(4)", "welding", ["cooling_water_low", "shielding_gas_low"], True),
    ("1910.254", "Q", "(d)(9)", "welding", [], True),
    ("1910.255", "Q", "(a)(4)", "welding", [], True),
    ("1910.255", "Q", "(b)(3)", "welding", [], True),
    ("1910.255", "Q", "(b)(5)", "welding", [], True),
    ("1910.255", "Q", "(b)(7)", "welding", [], True),
    ("1910.255", "Q", "(b)(8)", "welding", ["spot_electrode_wear"], True),
    ("1910.255", "Q", "(e)", "welding", [], True),
]


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
                eff = "2026-07-01"
                basis = "115-06-30 修正版本；除第 21-3、57、116、185-1～185-4 條外，自 115-07-01 施行（個別條文是否於該次修正變動未逐條查證）"
            pieces = split_long(text)
            for i, piece in enumerate(pieces):
                cid = f"REG-{pcode}-{no}" + (f"-p{i + 1}" if len(pieces) > 1 else "")
                out.append(_tw_chunk(cid, pcode, law, no, piece, line, fms, amend, eff, status, basis,
                                     f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={no}",
                                     part=(i + 1, len(pieces))))
    return out


def ecfr_chunks() -> list[dict]:
    out = []
    for sec, sub, para, line, fms, children in ECFR_PARAS:
        page = curl(ECFR.format(sec=sec), f"ecfr-{sec}.html")
        key = sec + para
        texts = []
        for m in re.finditer(r'<p[^>]*data-title="([^"]+)"[^>]*>(.*?)</p>', page, re.S):
            dtitle = html.unescape(m.group(1))
            if dtitle == key or (children and dtitle.startswith(key + "(")):
                t = html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))
                texts.append(re.sub(r"\s+", " ", t).strip())
        if not texts:
            print(f"🔬 missing {key} — left out")
            continue
        body = "\n".join(texts)
        words = body.split()
        if len(words) > 450:
            body = " ".join(words[:450]) + " […continued in source]"
        cid = f"REG-OSHA-{sec}" + re.sub(r"[()]", lambda x: "-" if x.group() == "(" else "", para)
        out.append({
            "chunk_id": cid, "doc_id": f"OSHA-{sec}", "doc_type": "regulation", "line": line,
            "machine_type": [], "failure_mode_ids": fms,
            "source": ECFR_PUBLIC.format(sub=sub, sec=sec), "license": "PD", "data_label": "公開資料", "lang": "en",
            "version": "eCFR current", "review_status": "approved",
            "law_name": f"29 CFR {sec}", "article": key, "effective_date": f"current (eCFR, retrieved {TODAY})",
            "effective_status": "in_force (US)", "title": f"29 CFR {key}",
            "text": f"29 CFR {key}\n{body}", "retrieved_at": TODAY,
            "sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
            "license_note": "US federal regulation, public domain (17 U.S.C. §105); text from official eCFR (osha.gov blocks scripted access)",
        })
    return out


# ---------------------------------------------------------------- guidance (PDF → text, verbatim)
def pdf_text(url: str, name: str) -> str:
    pdf = CACHE / name
    if not pdf.exists():
        curl(url, name)
    txt = CACHE / (name + ".txt")
    if not txt.exists():
        subprocess.run(["pdftotext", str(pdf), str(txt)], check=True)
    return txt.read_text(encoding="utf-8", errors="replace")


def tidy(t: str) -> str:
    """Remove layout artefacts only (page numbers, running headers, bullet glyphs); words untouched."""
    lines = []
    for ln in t.splitlines():
        s = ln.strip()
        if not s or re.fullmatch(r"\d{1,3}", s) or re.fullmatch(r"\d+ of \d+", s) or s == "NASA-STD-8739.4A":
            continue
        if s in ("■", "•", "••") or re.fullmatch(r"[■•\s]+", s):
            continue
        s = re.sub(r"^[■•]+\s*", "• ", s)
        lines.append(s)
    out = "\n".join(lines)
    out = re.sub(r"(?<![.:;)\]])\n(?=[a-z(])", " ", out)  # re-join hard-wrapped sentences
    return out.strip()


def between(t: str, start: str, end: str | None) -> str:
    i = t.index(start)
    j = t.index(end, i + len(start)) if end else len(t)
    return t[i:j]


GUIDE_DOCS = [
    {
        "doc": "HSE-INDG316", "code": "L12", "url": "https://www.hse.gov.uk/pubns/indg316.pdf", "file": "indg316.pdf",
        "title": "HSE INDG316 Procedures for daily inspection and testing of mechanical power presses and press brakes",
        "license": "OGL", "line": "stamping", "doc_type": "guideline",
        "license_note": "© Crown copyright. Document states: may be freely reproduced, except for advertising, endorsement or commercial purposes; please acknowledge the source as HSE. Contains public sector information published by the Health and Safety Executive.",
        "sections": [
            ("introduction", "INTRODUCTION", "Doing the inspection and tests", []),
            ("doing-tests", "Doing the inspection and tests", "FIXED GUARDS", []),
            ("fixed-guards-checks", "Power-off checks for fixed guards", "INTERLOCKING GUARDS", []),
            ("interlocking-general", "INTERLOCKING GUARDS", "Positive clutch presses", []),
            ("photo-electric-poweroff", "PHOTO-ELECTRIC SAFETY DEVICES", "Power-on checks for photo-electric safety devices", ["light_curtain_fault"]),
            ("photo-electric-poweron", "Power-on checks for photo-electric safety devices", "Mute check - press brakes", ["light_curtain_fault"]),
        ],
    },
    {
        "doc": "OSHA-FS-3647", "code": "W1", "url": "https://www.osha.gov/sites/default/files/publications/OSHA_FS-3647_Welding.pdf",
        "file": "osha-fs3647.pdf", "title": "OSHA Fact Sheet FS-3647 Controlling Hazardous Fume and Gases during Welding",
        "license": "PD", "line": "welding", "doc_type": "guideline",
        "license_note": "US federal government work, public domain (17 U.S.C. §105). The photo caption credits a third party; photos are not reproduced.",
        "sections": [
            ("what-is-in-fume", "What is in welding fume?", "Health effects of breathing welding fume", ["fume_extraction_fault", "shielding_gas_low"]),
            ("health-effects", "Health effects of breathing welding fume", "Welding and Hexavalent Chromium", ["fume_extraction_fault"]),
            ("reducing-exposure", "Reducing exposure to welding fume", "Some OSHA standards applicable to welding:", ["fume_extraction_fault"]),
        ],
    },
    {
        "doc": "NASA-STD-8739.4A-C4", "code": "A2",
        "url": "https://standards.nasa.gov/sites/default/files/standards/NASA/A/4/nasa-std-87394a_w_change_4_0.pdf",
        "file": "nasa-std-87394a.pdf",
        "title": "NASA-STD-8739.4A Change 4 Workmanship Standard for Crimping, Interconnecting Cables, Harnesses, and Wiring",
        "license": "PD", "line": "assembly", "doc_type": "standard",
        "license_note": "NASA technical standard (US Government work), publicly accessible at standards.nasa.gov; public domain in the US (17 U.S.C. §105).",
        "sections": [
            ("7.2.19-bend-radius", "7.2.19 The bend radius data", "7.2.21 The use of solder sleeves", ["cable_pinch_misroute"]),
            ("table-7-1", "Table 7-1. Bend Radii for Completed Interconnecting Cable or Harness\nWire", "1/ Outside Diameter (OD)", ["cable_pinch_misroute"]),
            ("7.2.28-support", "7.2.28 Provision shall be made for stress relief", "7.2.32 The use of splices", ["cable_pinch_misroute", "connector_not_seated"]),
            ("16.1-handling", "16.1.1 Interconnecting harnesses and cables often", "17.\n", ["cable_pinch_misroute"]),
            ("17.2.1-mating", "17.2.1 The following practices and precautions", "e. Harness connectors mated to test tees", ["connector_not_seated"]),
            ("20.5.1-ties-assemblies", "d. Cable and Harness Ties:", "(10)", ["cable_pinch_misroute", "connector_not_seated"]),
        ],
    },
]


def guidance_chunks() -> list[dict]:
    out = []
    for d in GUIDE_DOCS:
        try:
            raw = pdf_text(d["url"], d["file"])
        except Exception as e:  # noqa: BLE001
            print(f"🔬 {d['doc']} fetch failed ({type(e).__name__}) — left out")
            continue
        for sid, start, end, fms in d["sections"]:
            try:
                body = tidy(between(raw, start, end))
            except ValueError:
                print(f"🔬 {d['doc']} section {sid} not found — left out")
                continue
            if sid == "table-7-1":  # pdftotext puts each cell on its own line; keep cells, mark rows
                body = body.replace("\n", " | ")
            words = body.split()
            if len(words) > 420:
                body = " ".join(words[:420]) + " […continued in source]"
            out.append({
                "chunk_id": f"PUB-{d['doc']}-{sid}", "doc_id": d["doc"], "doc_type": d["doc_type"], "line": d["line"],
                "machine_type": [], "failure_mode_ids": fms, "source": d["url"], "source_code": d["code"],
                "license": d["license"], "license_note": d["license_note"], "data_label": "公開資料", "lang": "en",
                "version": "retrieved " + TODAY, "review_status": "approved", "parent_id": None,
                "title": f"{d['title']} — {sid}", "text": f"{d['title']} — {sid}\n{body}", "retrieved_at": TODAY,
                "sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
                "extraction": "pdftotext；只移除頁碼、頁首與項目符號等排版字元，文字逐字未改寫",
            })
    return out


def write(path, chunks):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"wrote {len(chunks)} chunks -> {path.relative_to(KB)}")


def main():
    write(OUT_REG, tw_chunks() + ecfr_chunks())
    write(OUT_GUIDE, guidance_chunks())


if __name__ == "__main__":
    main()
