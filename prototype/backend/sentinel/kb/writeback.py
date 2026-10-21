"""知識回寫（CL-3）：結案回報 → 案例草稿（review_status=draft）→ 主管核准 → 立即寫入 cases collection。

「越用越準」：核准後的案例馬上成為下一次 kb.similar_cases 的候選（同機台、同事件類型排最前面），
所以同一個異常再發生時，Jev J3 的 state.similar_cases 會帶到這筆真實結案紀錄。
草稿放在 settings.paths.drafts/{line}/（預設 knowledge/cases/_drafts/），make kb 重建時也會照守門規則讀到。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import ingest as ing
from .retrieve import KnowledgeBase

log = logging.getLogger(__name__)

LINE_PREFIX = {"stamping": "STP", "welding": "WLD", "assembly": "ASM"}


def case_id_for(line: str, workorder_id: str, at: datetime) -> str:
    return f"CASE-{LINE_PREFIX.get(line, line[:3].upper())}-WB{at:%Y%m%d}-{workorder_id.split('-')[-1]}"


def build_case(*, case_id: str, inc: Any, machine: Any, wo_id: str, report: str, final_rc: str | None,
               diag: Any, timeline: list[str], closed_at: datetime, reported_by: str) -> dict:
    top1 = (diag.root_cause_top3 or [None])[0] if diag else None
    facts = {k: v for k, v in (inc.facts or {}).items() if v is not None and k not in ("source",)}
    logs = [{"t": inc.opened_at.isoformat(), "signal": k, "value": v} for k, v in facts.items()
            if isinstance(v, (int, float, str, bool))]
    return {
        "case_id": case_id, "data_label": "模擬資料", "synthetic": True,
        "generator": {"source": "writeback", "workorder_id": wo_id},
        "line": inc.line, "machine_type": machine.type if machine else None, "machine_id": inc.machine_id,
        "opened_at": inc.opened_at.isoformat(), "closed_at": closed_at.isoformat(), "case_type": "writeback",
        "trigger": {"source": (inc.facts or {}).get("source", "fused"), "event_type": inc.type,
                    "rule_severity": inc.rule_severity, "rule_id": inc.rule_id},
        "context": {"days_since_pm": getattr(machine, "days_since_pm", None), "timeline": timeline[-10:]},
        "log_signals": logs,
        "technician": reported_by,
        "technician_report_raw": report,
        "technician_report_normalized": report,
        "first_hypothesis": {"failure_mode_id": top1.get("id"), "by": "jev", "p": top1.get("p")} if top1 else None,
        "final_root_cause": final_rc,
        "hypothesis_correct": (top1.get("id") == final_rc) if top1 and final_rc else None,
        "related_failure_mode_ids": [final_rc] if final_rc else [],
        "lessons": f"現場確認根因為 {final_rc}" if final_rc else "",
        "sop_used": diag.sop_ids if diag else [],
        "review_status": "draft",
    }


class CaseWriteback:
    def __init__(self, kb: KnowledgeBase, drafts_dir: Path):
        self.kb = kb
        self.dir = drafts_dir

    def path(self, case_id: str, line: str | None = None) -> Path | None:
        if line:
            return self.dir / line / f"{case_id}.json"
        hits = list(self.dir.glob(f"*/{case_id}.json"))
        return hits[0] if hits else None

    def write(self, doc: dict) -> Path:
        p = self.dir / doc["line"] / f"{doc['case_id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def get(self, case_id: str) -> dict | None:
        p = self.path(case_id)
        return json.loads(p.read_text(encoding="utf-8")) if p else None

    def status(self, case_id: str | None) -> str | None:
        d = self.get(case_id) if case_id else None
        return d.get("review_status") if d else None

    def list(self, status: str | None = None) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*/CASE-*.json")) if self.dir.exists() else []:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if status and d.get("review_status") != status:
                continue
            out.append(summary(d))
        return out

    def approve(self, case_id: str, *, reviewer: str, at: datetime, note: str = "現場主管核准") -> tuple[dict, int]:
        """改成 approved、寫回檔案、立即切塊寫入 cases collection（不重建整個知識庫）。回傳 (案例, 寫入 chunk 數)。"""
        p = self.path(case_id)
        if p is None:
            raise KeyError(case_id)
        d = json.loads(p.read_text(encoding="utf-8"))
        d["review_status"] = "approved"
        d["reviewed_by"] = sorted(set((d.get("reviewed_by") or []) + [reviewer]))
        d["review_note"] = note
        d["reviewed_at"] = at.isoformat()
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        chunks = ing.chunk_case(d)
        reason = ing.guard(chunks[0]["metadata"])
        if reason:
            raise ValueError(f"守門拒收：{reason}")
        n = self.kb.store.upsert("cases", chunks) if self.kb.store is not None else 0
        return d, n


def summary(d: dict) -> dict:
    return {"case_id": d["case_id"], "status": d.get("review_status"), "line": d.get("line"),
            "machine_id": d.get("machine_id"), "machine_type": d.get("machine_type"),
            "workorder_id": (d.get("generator") or {}).get("workorder_id"),
            "event_type": (d.get("trigger") or {}).get("event_type"), "final_root_cause": d.get("final_root_cause"),
            "first_hypothesis": d.get("first_hypothesis"), "hypothesis_correct": d.get("hypothesis_correct"),
            "report": d.get("technician_report_raw"), "closed_at": d.get("closed_at"),
            "reviewed_by": d.get("reviewed_by"), "reviewed_at": d.get("reviewed_at"), "review_note": d.get("review_note")}
