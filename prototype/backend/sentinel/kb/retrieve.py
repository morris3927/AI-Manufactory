"""知識庫檢索（KB-2；07_RAG知識庫資料.md 4.6）。

- similar_cases：向量相似度，篩 line、machine_type
- sops_for：直接用故障模式 ID 查，不靠語意（可稽核：同一根因一定取到同一份 SOP）
- loto_for：讀 YAML，不進向量庫
- search：一般語意檢索（信心不足時）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import ingest as ing
from .store import VectorStore

log = logging.getLogger(__name__)


class CaseSummary(BaseModel):
    case_id: str
    failure_mode_id: str | None = None
    hypothesis_correct: bool | None = None
    short: str
    distance: float | None = None


class SopChunk(BaseModel):
    sop_id: str
    phase: str
    text: str
    failure_mode_ids: list[str] = Field(default_factory=list)
    loto_required: bool = False
    title: str = ""


class LotoStep(BaseModel):
    no: int
    action: str
    check: str | None = None


class LotoChecklist(BaseModel):
    loto_id: str
    machine_type: str | list[str]
    steps: list[LotoStep]
    energy_sources: list[dict[str, Any]] = Field(default_factory=list)
    release: list[str] = Field(default_factory=list)
    permit_required: bool = False
    legal_basis: list[str] = Field(default_factory=list)
    ai_constraints: list[str] = Field(default_factory=list)
    data_label: str = "模擬資料"
    is_default: bool = False


class Chunk(BaseModel):
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    distance: float | None = None


# knowledge/loto 還沒有對應機型時的通用檢查表（依設施規則第 57 條；模擬資料）
DEFAULT_LOTO = LotoChecklist(
    loto_id="LOTO-GENERIC-00", machine_type="*", is_default=True, permit_required=False,
    legal_basis=["設施規則第57條（停機、上鎖掛牌、作業許可、殘壓處理）",
                 "由原上鎖人親自取下自己的鎖（參考 OSHA 29 CFR 1910.147(e)(3)；電路作業依設施規則第254條確認無感電之虞後才送電）"],
    steps=[
        LotoStep(no=1, action="通知操作員與受影響人員，說明將停機維修", check="簽名"),
        LotoStep(no=2, action="以正常程序停機，停止送料", check="目視"),
        LotoStep(no=3, action="切斷主電源並逐一隔離所有能量源（電、氣、油壓、重力）", check="每項勾選"),
        LotoStep(no=4, action="每位作業人員在隔離點掛上個人鎖與標籤", check="鎖號登記"),
        LotoStep(no=5, action="洩除殘壓並驗證零能量：按起動鈕確認無動作後歸回 OFF", check="兩人確認"),
    ],
    release=["清點工具、恢復護圍", "確認人員離開危險區", "各人取下自己的鎖（不得代取）", "通知受影響人員後試運轉"],
)

DEFAULT_CHECKLIST = ["確認異常原因已排除", "無料空轉與試沖確認正常", "護圍、光幕與現場整理復原"]


class KnowledgeBase:
    def __init__(self, root: Path, store: VectorStore | None, allow_draft: bool = False):
        self.root = root
        self.store = store
        self.allow_draft = allow_draft
        self.reload()

    def reload(self) -> None:
        self.failure_modes = ing.load_failure_modes(self.root) if self.root.exists() else {}
        self.sops: dict[str, dict] = {}
        for d in (ing.load_sops(self.root) if self.root.exists() else []):
            meta = ing._base_meta(d, "sop", d["sop_id"])
            if ing.guard(meta, self.allow_draft) is None:
                self.sops[d["sop_id"]] = d
        self.lotos = ing.load_lotos(self.root) if self.root.exists() else []
        log.info("知識庫：故障模式 %d、SOP %d、LOTO %d", sum(map(len, self.failure_modes.values())),
                 len(self.sops), len(self.lotos))

    # ── 故障模式 ───────────────────────────────────────────────
    def failure_modes_for(self, line: str, machine_type: str | None = None) -> dict[str, dict]:
        modes = self.failure_modes.get(line, {})
        if machine_type:
            filtered = {k: v for k, v in modes.items()
                        if not (v.get("machine_types") or v.get("machine_type"))
                        or machine_type in ing._as_list(v.get("machine_types") or v.get("machine_type"))}
            return filtered or modes
        return modes

    def severity_notes(self, line: str, machine_type: str | None = None) -> list[str]:
        """故障模式的 severity_note（S3 條件），補進 Jev 嚴重度題目的標準。"""
        return [f"{fm.get('label', fid)}：{fm['severity_note']}"
                for fid, fm in self.failure_modes_for(line, machine_type).items() if fm.get("severity_note")]

    def label(self, line: str, fid: str) -> str:
        return (self.failure_modes.get(line, {}).get(fid) or {}).get("label", fid)

    # ── 案例 ───────────────────────────────────────────────────
    def similar_cases(self, incident: Any, k: int = 5, machine_type: str | None = None,
                      query: str | None = None) -> list[CaseSummary]:
        if self.store is None:
            return []
        q = query or f"{incident.type} {incident.machine_id or ''} " + " ".join(
            f"{k}={v}" for k, v in (incident.facts or {}).items())
        where: dict[str, Any] = {"$and": [{"line": incident.line}, {"chunk_kind": "case_summary"}]}
        if machine_type:
            where["$and"].append({"machine_type": machine_type})
        try:
            hits = self.store.query("cases", q, k=k * 4, where=where)
            if not hits and machine_type:
                hits = self.store.query("cases", q, k=k * 4, where={"$and": where["$and"][:2]})
        except Exception:
            log.exception("案例檢索失敗")
            return []
        # 語意相似之外，同一台機台、同一種事件的案例排前面（結案回寫的真實紀錄因此會排第一）
        mid = getattr(incident, "machine_id", None)
        hits.sort(key=lambda h: (h["metadata"].get("machine_id") != mid,
                                 h["metadata"].get("trigger_type") != incident.type, h.get("distance") or 0))
        out = []
        for h in hits[:k]:
            m = h["metadata"]
            out.append(CaseSummary(case_id=m.get("case_id", h["chunk_id"]), failure_mode_id=m.get("failure_mode_id"),
                                   hypothesis_correct=m.get("hypothesis_correct"), short=h["text"][:300],
                                   distance=h.get("distance")))
        return out

    # ── SOP（用 ID 查）─────────────────────────────────────────
    def sop_ids_for(self, failure_mode_ids: list[str], line: str | None = None) -> list[str]:
        ids: list[str] = []
        for fid in failure_mode_ids:
            for sid, d in self.sops.items():
                if fid in ing._as_list(d.get("failure_mode_ids")) and sid not in ids:
                    ids.append(sid)
            for ln, modes in self.failure_modes.items():          # 故障模式目錄上的 sop 欄位
                if line and ln != line:
                    continue
                sid = (modes.get(fid) or {}).get("sop")
                if sid and sid in self.sops and sid not in ids:
                    ids.append(sid)
        return ids

    def sops_for(self, failure_mode_ids: list[str], phases: list[str] | None = None) -> list[SopChunk]:
        out: list[SopChunk] = []
        for sid in self.sop_ids_for(failure_mode_ids):
            d = self.sops[sid]
            for c in ing.chunk_sop(d):
                ph = c["metadata"]["phase"]
                if phases is None or ph in phases:
                    out.append(SopChunk(sop_id=sid, phase=ph, text=c["text"], title=d.get("title", ""),
                                        failure_mode_ids=ing._as_list(d.get("failure_mode_ids")),
                                        loto_required=c["metadata"]["loto_required"]))
        return out

    def plan_sops(self, top3: list[Any], *, top1_thr: float = 0.6, low_thr: float = 0.3) -> dict[str, Any]:
        """依 Jev 根因信心度決定取哪些 SOP（4.6 第 ③ 步）。top3：[{id, p}]。"""
        if not top3:
            return {"mode": "no_root_cause", "chunks": [], "note": "無根因判斷，請工程師確認"}
        first = top3[0]
        fid, p = (first["id"], first["p"]) if isinstance(first, dict) else (first.id, first.p)
        if fid == "new" or p < low_thr:
            return {"mode": "semantic", "chunks": [], "note": "無對應 SOP，請工程師確認"}
        if p >= top1_thr:
            return {"mode": "top1_all_phases", "chunks": self.sops_for([fid])}
        ids = [(t["id"] if isinstance(t, dict) else t.id) for t in top3]
        return {"mode": "top3_inspect", "chunks": self.sops_for(ids, phases=["summary", "inspect"])}

    def checklist_for(self, failure_mode_id: str | None) -> list[str]:
        if failure_mode_id:
            for sid in self.sop_ids_for([failure_mode_id]):
                items = [str(x) for x in ing._as_list(self.sops[sid].get("verify"))]
                if items:
                    return items
        return list(DEFAULT_CHECKLIST)

    # ── LOTO（不進向量庫）──────────────────────────────────────
    def loto_for(self, machine_type: str | None, loto_id: str | None = None) -> LotoChecklist:
        for d in self.lotos:
            if (loto_id and d.get("loto_id") == loto_id) or \
                    (not loto_id and machine_type in ing._as_list(d.get("machine_type"))):
                return LotoChecklist(
                    loto_id=d["loto_id"], machine_type=d.get("machine_type", machine_type or "*"),
                    steps=[LotoStep(no=ing.step_no(s, i + 1), action=str(s.get("action", "")),
                                    check=s.get("check")) for i, s in enumerate(d.get("steps") or [])],
                    energy_sources=d.get("energy_sources") or [], release=[str(x) for x in d.get("release") or []],
                    permit_required=bool((d.get("permit") or {}).get("required")),
                    legal_basis=[str(x) for x in d.get("legal_basis") or []],
                    ai_constraints=[str(x) for x in d.get("ai_constraints") or []])
        if loto_id:
            return self.loto_for(machine_type)
        return DEFAULT_LOTO

    # ── 一般語意檢索 ───────────────────────────────────────────
    def search(self, query: str, filters: dict[str, Any] | None = None, k: int = 5,
               collection: str = "docs") -> list[Chunk]:
        if self.store is None:
            return []
        where = None
        if filters:
            conds = [{k: v} for k, v in filters.items()]
            where = conds[0] if len(conds) == 1 else {"$and": conds}
        try:
            return [Chunk(**h) for h in self.store.query(collection, query, k=k, where=where)]
        except Exception:
            log.exception("語意檢索失敗")
            return []
