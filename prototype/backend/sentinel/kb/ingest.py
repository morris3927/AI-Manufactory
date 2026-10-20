"""知識庫入庫（KB-1）：讀取 prototype/knowledge/，依 07_RAG知識庫資料.md 4.5 切塊後寫入 Chroma。

入庫守門：review_status != approved 或 license 為空 → 拒收（--allow-draft 可放行 draft／reviewed，只限 Demo）；
license = © 一律拒收（只能出現在 SOP 的 references 欄位）。LOTO 不切塊、不進向量庫。
cases/**/heldout/ 是保留測試集，不管有沒有 --allow-draft 都不入庫（eval/testset.py 另外讀取）。

用法：python -m sentinel.kb.ingest [--allow-draft] [--rebuild]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

PHASES = ["prepare", "isolate", "inspect", "repair", "verify", "restore"]
PHASE_LABEL = {"prepare": "準備", "isolate": "隔離（LOTO）", "inspect": "檢查", "repair": "修復",
               "verify": "驗證", "restore": "復原"}
MAX_CHARS = 500


# ── 讀檔 ───────────────────────────────────────────────────────
def _sorted(paths) -> list[Path]:
    """原始檔排前面，檔名有空白的副本（「… 2.json」）排後面。"""
    return sorted(paths, key=lambda p: (" " in p.name, str(p)))


def _dedupe(docs: list[dict], key: str) -> list[dict]:
    """同一個 ID 只取一份（例如 Finder 複製出的「CASE-STP-0003 2.json」）；依檔名排序，取第一個。"""
    seen: dict[str, dict] = {}
    for d in docs:
        if d[key] in seen:
            log.warning("重複的 %s=%s，略過後面的檔案", key, d[key])
            continue
        seen[d[key]] = d
    return list(seen.values())


def _read_yaml(p: Path) -> Any:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_failure_modes(root: Path) -> dict[str, dict[str, dict]]:
    """回傳 {line: {failure_mode_id: {...}}}。檔案頂層可以是 {line: {...}} 或直接 {id: {...}}。"""
    out: dict[str, dict[str, dict]] = {}
    for p in _sorted((root / "failure_modes").glob("*.y*ml")):
        try:
            data = _read_yaml(p) or {}
        except yaml.YAMLError as e:
            log.warning("無法解析 %s：%s", p, e)
            continue
        if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict) \
                and all(isinstance(v, dict) for v in next(iter(data.values())).values()):
            line, modes = next(iter(data.items()))
        else:
            line, modes = p.stem, data
        out.setdefault(str(line), {}).update({k: v for k, v in (modes or {}).items() if isinstance(v, dict)})
    return out


def load_sops(root: Path) -> list[dict]:
    docs = []
    for p in _sorted((root / "sop").glob("**/SOP-*.y*ml")):
        try:
            d = _read_yaml(p)
        except yaml.YAMLError as e:
            log.warning("無法解析 %s：%s", p, e)
            continue
        if isinstance(d, dict) and d.get("sop_id"):
            d.setdefault("line", p.parent.name)
            docs.append(d)
    return _dedupe(docs, "sop_id")


HELDOUT_DIR = "heldout"     # 保留測試集：只能給 eval/ 用，永遠不入庫


def load_cases(root: Path, *, heldout: bool = False) -> list[dict]:
    """heldout=False（預設）：入庫用，排除 cases/**/heldout/；heldout=True：只讀保留測試集（eval 用）。"""
    docs = []
    for p in _sorted((root / "cases").glob("**/CASE-*.json")):
        if (HELDOUT_DIR in p.relative_to(root).parts) != heldout:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.warning("無法解析 %s：%s", p, e)
            continue
        if isinstance(d, dict) and d.get("case_id"):
            docs.append(d)
    return _dedupe(docs, "case_id")


def load_lotos(root: Path) -> list[dict]:
    docs = []
    for p in _sorted((root / "loto").glob("LOTO-*.y*ml")):
        try:
            d = _read_yaml(p)
        except yaml.YAMLError as e:
            log.warning("無法解析 %s：%s", p, e)
            continue
        if isinstance(d, dict) and d.get("loto_id"):
            docs.append(d)
    return _dedupe(docs, "loto_id")


def load_public(root: Path) -> list[dict]:
    """public/*.jsonl：每行一個 chunk，{text, metadata{...}} 或扁平欄位。"""
    rows = []
    for p in _sorted((root / "public").glob("**/*.jsonl")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                log.warning("%s 第 %d 行不是 JSON", p, i + 1)
                continue
            meta = dict(d.get("metadata") or {k: v for k, v in d.items() if k not in {"text", "chunk_id"}})
            meta.setdefault("doc_id", p.stem)
            rows.append({"chunk_id": d.get("chunk_id") or meta.get("chunk_id") or f"{p.stem}-{i:04d}",
                         "text": d.get("text", ""), "metadata": meta})
    return rows


# ── 守門 ───────────────────────────────────────────────────────
def internal_license(doc: dict) -> str | None:
    if doc.get("license"):
        return doc["license"]
    if doc.get("synthetic") or doc.get("data_label") == "模擬資料":
        return "internal-synthetic"
    return None


def guard(meta: dict, allow_draft: bool = False) -> str | None:
    """回傳拒收原因；None 表示可以入庫。"""
    lic = str(meta.get("license") or "").strip()
    if not lic:
        return "缺少 license"
    if "©" in lic or lic.lower() in {"(c)", "copyright"}:
        return "license=© 不能進向量庫"
    status = meta.get("review_status")
    if status != "approved":
        if allow_draft and status in {"draft", "reviewed"}:
            return None
        return f"review_status={status}"
    return None


# ── 切塊 ───────────────────────────────────────────────────────
def split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    parts, cur = [], ""
    for sent in re.split(r"(?<=[。；！？\n.;!?])", text):
        if len(cur) + len(sent) > max_chars and cur:
            parts.append(cur.strip())
            cur = ""
        cur += sent
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _base_meta(doc: dict, doc_type: str, doc_id: str) -> dict:
    return {
        "doc_id": doc_id, "doc_type": doc_type, "line": doc.get("line", "all"),
        "machine_type": _as_list(doc.get("machine_type")), "source": doc.get("source", "internal"),
        "license": internal_license(doc), "data_label": doc.get("data_label", "模擬資料"),
        "lang": doc.get("lang", "zh-TW"), "version": str(doc.get("version", "")),
        "review_status": doc.get("review_status"),
    }


def step_no(s: dict, default: int = 0) -> int:
    """步驟編號：YAML 1.1 會把沒加引號的 no 讀成 False，所以兩種 key 都接受。"""
    v = s.get("no", s.get(False, default))  # type: ignore[call-overload]
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _step_line(s: dict) -> str:
    t = f"{step_no(s)}. {s.get('action', '')}"
    if s.get("caution"):
        t += f"（注意：{s['caution']}）"
    if s.get("criteria"):
        t += f"［判定：{s['criteria']}］"
    if s.get("requires_supervisor"):
        t += "［需主管確認］"
    return t


def sop_phases(doc: dict) -> dict[str, list[str]]:
    phases: dict[str, list[str]] = {}
    for s in doc.get("steps") or []:
        phases.setdefault(s.get("phase", "inspect"), []).append(_step_line(s))
    for extra in ("verify", "restore"):
        for item in _as_list(doc.get(extra)):
            phases.setdefault(extra, []).append(f"- {item}")
    return phases


def chunk_sop(doc: dict) -> list[dict]:
    sid = doc["sop_id"]
    safety = doc.get("safety") or {}
    meta = _base_meta(doc, "sop", sid) | {"sop_id": sid, "failure_mode_ids": _as_list(doc.get("failure_mode_ids")),
                                          "loto_required": bool(safety.get("loto_required"))}
    summary = (f"{sid}《{doc.get('title', '')}》適用：{'、'.join(_as_list(doc.get('machine_type')))}。"
               f"故障模式：{'、'.join(meta['failure_mode_ids'])}。徵兆：{'；'.join(_as_list(doc.get('symptoms')))}。"
               f"LOTO：{'需要' if safety.get('loto_required') else '不需要'}（{safety.get('loto_checklist_id', '-')}）。"
               f"PPE：{'、'.join(_as_list(safety.get('ppe')))}。")
    if doc.get("escalate_if"):
        summary += f"升級條件：{'；'.join(_as_list(doc['escalate_if']))}"
    chunks = [{"chunk_id": f"{sid}#summary", "text": summary, "metadata": meta | {"phase": "summary"}}]
    for phase, lines in sop_phases(doc).items():
        for i, part in enumerate(split_text(f"{sid} {PHASE_LABEL.get(phase, phase)}：\n" + "\n".join(lines))):
            chunks.append({"chunk_id": f"{sid}#{phase}" + (f"-{i}" if i else ""), "text": part,
                           "metadata": meta | {"phase": phase}})
    return chunks


def case_short(doc: dict) -> str:
    fh = doc.get("first_hypothesis") or {}
    s = (f"{doc.get('case_id')}｜{doc.get('machine_type')}｜觸發 {(doc.get('trigger') or {}).get('event_type', '-')}｜"
         f"最終根因 {doc.get('final_root_cause')}")
    if fh and doc.get("hypothesis_correct") is False:
        s += f"｜第一判斷 {fh.get('failure_mode_id')} 錯誤：{doc.get('why_first_guess_wrong', '')}"
    if doc.get("discriminating_evidence"):
        s += f"｜區分證據：{'、'.join(_as_list(doc['discriminating_evidence']))}"
    return s


def chunk_case(doc: dict) -> list[dict]:
    cid = doc["case_id"]
    meta = _base_meta(doc, "case", cid) | {
        "case_id": cid, "failure_mode_id": doc.get("final_root_cause"),
        "failure_mode_ids": _as_list(doc.get("related_failure_mode_ids"))
        or ([doc.get("final_root_cause")] if doc.get("final_root_cause") else []),
        "case_type": doc.get("case_type"),
        "hypothesis_correct": doc.get("hypothesis_correct"), "machine_id": doc.get("machine_id"),
    }
    meta["machine_type"] = doc.get("machine_type") if isinstance(doc.get("machine_type"), str) else \
        "|".join(_as_list(doc.get("machine_type")))
    logs = "；".join(f"{x.get('t')} {x.get('signal')}={x.get('value')}" for x in doc.get("log_signals") or [])
    ctx = doc.get("context") or {}
    summary = (case_short(doc) + f"。距上次保養 {ctx.get('days_since_pm', '-')} 天；近期變更："
               f"{'、'.join(_as_list(ctx.get('recent_changes'))) or '無'}。Log：{logs}。教訓：{doc.get('lessons', '')}")
    actions = "；".join(f"{a.get('step')}→{a.get('result')}" for a in doc.get("actions") or [])
    detail = (f"{cid} 處置：{actions}。回報：{doc.get('technician_report_normalized') or doc.get('technician_report_raw', '')}。"
              f"使用 SOP：{'、'.join(_as_list(doc.get('sop_used')))}；停機 {doc.get('downtime_min', '-')} 分鐘；"
              f"結果 {doc.get('outcome', '-')}")
    return [
        {"chunk_id": f"{cid}#summary", "text": split_text(summary, 800)[0], "metadata": meta | {"chunk_kind": "case_summary"}},
        {"chunk_id": f"{cid}#detail", "text": split_text(detail, 800)[0], "metadata": meta | {"chunk_kind": "case_detail"}},
    ]


def chunk_failure_modes(line: str, modes: dict[str, dict]) -> list[dict]:
    out = []
    for fid, fm in modes.items():
        doc = {"line": line, "review_status": fm.get("review_status", "approved"),
               "machine_type": fm.get("machine_types") or fm.get("machine_type"), **fm}
        meta = _base_meta(doc, "failure_mode", fid) | {"failure_mode_id": fid, "failure_mode_ids": [fid]}
        text = (f"{fid}（{fm.get('label', '')}）判斷依據：{fm.get('criteria', '')}。"
                f"症狀：{'、'.join(_as_list(fm.get('symptoms')))}。SOP：{fm.get('sop', '-')}")
        out.append({"chunk_id": f"FM:{line}:{fid}", "text": text, "metadata": meta})
    return out


def chunk_public(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        meta = {"doc_type": "reference", "line": "all", "data_label": "公開資料", **r["metadata"]}
        for i, part in enumerate(split_text(r["text"], 600)):
            out.append({"chunk_id": r["chunk_id"] + (f"-{i}" if i else ""), "text": part, "metadata": meta})
    return out


# ── 入庫 ───────────────────────────────────────────────────────
@dataclass
class IngestReport:
    accepted: dict[str, int] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def add(self, kind: str, n: int = 1) -> None:
        self.accepted[kind] = self.accepted.get(kind, 0) + n


def collect_chunks(root: Path, allow_draft: bool = False) -> tuple[dict[str, list[dict]], IngestReport]:
    rep = IngestReport()
    out: dict[str, list[dict]] = {"docs": [], "cases": []}

    def admit(target: str, kind: str, chunks: list[dict], doc_id: str) -> None:
        if not chunks:
            return
        reason = guard(chunks[0]["metadata"], allow_draft)
        if reason:
            rep.rejected.append((doc_id, reason))
            return
        out[target].extend(chunks)
        rep.add(kind)

    for line, modes in load_failure_modes(root).items():
        for c in chunk_failure_modes(line, modes):
            admit("docs", "failure_mode", [c], c["metadata"]["doc_id"])
    for d in load_sops(root):
        admit("docs", "sop", chunk_sop(d), d["sop_id"])
    for d in load_cases(root):
        admit("cases", "case", chunk_case(d), d["case_id"])
    for c in chunk_public(load_public(root)):
        admit("docs", "public", [c], c["chunk_id"])
    return out, rep


def ingest(root: Path, store, allow_draft: bool = False, rebuild: bool = False) -> IngestReport:
    if rebuild:
        store.reset()
    chunks, rep = collect_chunks(root, allow_draft)
    for name, items in chunks.items():
        store.upsert(name, items)
    return rep


def main(argv: list[str] | None = None) -> int:
    from ..config import load_settings
    from .store import VectorStore, get_embedder

    ap = argparse.ArgumentParser(description="建置 Sentinel 知識庫（Chroma）")
    ap.add_argument("--allow-draft", action="store_true", help="放行 review_status=draft／reviewed（只供開發）")
    ap.add_argument("--rebuild", action="store_true", help="先清空 collection")
    ap.add_argument("--knowledge", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = load_settings()
    root = args.knowledge or s.path("knowledge")
    store = VectorStore(s.path("chroma"), get_embedder(s.kb.embedding, s.kb.embedding_model))
    rep = ingest(root, store, allow_draft=args.allow_draft or s.kb.allow_draft, rebuild=args.rebuild)
    print(f"知識庫：{root}")
    print(f"入庫：{rep.accepted or '無'}；docs={store.count('docs')} cases={store.count('cases')}")
    if rep.rejected:
        print(f"拒收 {len(rep.rejected)} 筆：")
        for doc_id, reason in rep.rejected[:50]:
            print(f"  - {doc_id}：{reason}")
    print(f"LOTO 檢查表（不進向量庫）：{len(load_lotos(root))} 份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
