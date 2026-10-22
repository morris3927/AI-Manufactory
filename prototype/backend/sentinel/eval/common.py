"""評估實驗共用：路徑、金鑰、Jev／LLM client、評估用知識庫、案例 → 事件 state、統計。

所有實驗都只讀 knowledge/（不修改），保留測試集 cases/**/heldout/ 不入庫，
評估用向量庫另外建在 eval/.cache/chroma（不動 backend/chroma）。
"""
from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import PROTOTYPE_DIR, load_settings
from ..jev.client import JevClient
from ..kb import ingest as ing
from ..kb.facts import signal_facts  # noqa: F401（案例 Log → 事件 facts；與檢索用同一份）
from ..kb.retrieve import KnowledgeBase
from ..kb.store import VectorStore, get_embedder

EVAL_DIR = PROTOTYPE_DIR / "eval"
RESULTS = EVAL_DIR / "results"
FIGURES = RESULTS / "figures"
RAW = RESULTS / "raw"
CACHE = EVAL_DIR / ".cache"
KNOWLEDGE = PROTOTYPE_DIR / "knowledge"
# 外部影像（SH17、Wikimedia）的本機暫存，不放進 repo
SCRATCH = Path(os.environ.get("SENTINEL_EVAL_SCRATCH", Path.home() / ".cache" / "sentinel-eval"))

for _d in (RESULTS, FIGURES, RAW, CACHE, SCRATCH):
    _d.mkdir(parents=True, exist_ok=True)

SETTINGS = load_settings()
# 評估用 embedder：預設與正式設定相同（config.yaml kb.embedding／embedding_model／index_text／retrieval）；
# SENTINEL_EVAL_EMBEDDER=hash 改用 hash-512 退路（2026-10-21 第一版 E1–E3 用的就是它），結果檔名加 _hash 並列比較
EMBEDDER_KIND = os.environ.get("SENTINEL_EVAL_EMBEDDER", SETTINGS.kb.embedding)
SEV_RANK = {"S1": 1, "S2": 2, "S3": 3}


def jev_client(timeout_s: float = 10.0) -> JevClient:
    """評估用 timeout 放寬到 10 秒（正式 3 秒），逾時另外計數，不影響延遲統計的真實性。"""
    s = SETTINGS
    return JevClient(api_key=s.secret(s.secrets.openrouter_api_key), url=s.jev.url, model=s.jev.model,
                     timeout_s=timeout_s, db=None)


def ollama_key() -> str | None:
    return SETTINGS.secret(SETTINGS.secrets.ollama_api_key)


# ── 統計 ───────────────────────────────────────────────────────
def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q                       # 線性內插
    lo, hi = math.floor(k), math.ceil(k)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def lat_stats(vals: list[float]) -> dict[str, Any]:
    return {"n": len(vals), "mean": round(statistics.fmean(vals), 1) if vals else None,
            "p50": pct(vals, 0.5), "p95": pct(vals, 0.95), "max": round(max(vals), 1) if vals else None}


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """二項比例的 95% Wilson 信賴區間（小樣本時比常態近似可靠）。"""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0, c - h), 3), round(min(1, c + h), 3)]


def rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 3) if n else None, "ci95": wilson(k, n)}


def save_json(name: str, data: Any) -> Path:
    p = RESULTS / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def load_json(name: str) -> Any:
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ── 評估用知識庫（只收非 heldout 案例）─────────────────────────
_KB: KnowledgeBase | None = None


def _store_path(emb_name: str) -> Path:
    return CACHE / f"chroma-{emb_name}-{SETTINGS.kb.index_text}"


def eval_kb(rebuild: bool = False) -> KnowledgeBase:
    """與正式環境相同的 KnowledgeBase／ingest／檢索設定；allow_draft=True（案例目前多為 approved，放行少數 draft）。
    評估用向量庫另外建在 eval/.cache/chroma-<embedder>-<index_text>（不動 backend/chroma）。"""
    global _KB
    if _KB is not None and not rebuild:
        return _KB
    s = SETTINGS
    emb = get_embedder(EMBEDDER_KIND, s.kb.embedding_model)
    path = _store_path(emb.name)
    store = VectorStore(path, emb, index_text=s.kb.index_text)
    marker = path.with_name(path.name + ".ok")
    if rebuild or not marker.exists() or store.count("cases") == 0:
        rep = ing.ingest(KNOWLEDGE, store, allow_draft=True, rebuild=True)
        marker.write_text(json.dumps({"accepted": rep.accepted, "rejected": len(rep.rejected),
                                      "embedder": store.embedder.name, "index_text": s.kb.index_text},
                                     ensure_ascii=False))
    _KB = KnowledgeBase(KNOWLEDGE, store, allow_draft=True, retrieval=s.kb.retrieval)
    return _KB


def embedder_tag() -> str:
    """結果檔名後綴：正式設定的 embedder 用原檔名；hash 退路加 _hash；其他 embedder 加 _<名稱>。"""
    kb = eval_kb()
    name = kb.store.embedder.name if kb.store else "none"
    if name.startswith("hash"):
        return "_hash"
    return "" if name == SETTINGS.kb.embedding_model.split("/")[-1] else "_" + name.replace("/", "-")


def kb_info() -> dict:
    kb = eval_kb()
    p = _store_path(kb.store.embedder.name if kb.store else "none")
    info = json.loads(p.with_name(p.name + ".ok").read_text(encoding="utf-8")) if p.with_name(p.name + ".ok").exists() else {}
    return info | {"retrieval": kb.retrieval.model_dump()}


# ── 案例 → 事件 state（與正式 judge.build_state 相同的欄位）─────────
@dataclass
class EvalIncident:
    """模仿 db.models.Incident 中 judge.build_state／kb.similar_cases 會讀到的欄位。"""
    line: str
    type: str
    machine_id: str | None
    rule_severity: str | None
    facts: dict[str, Any]
    camera_id: str | None = None
    trace_id: str | None = None


@dataclass
class EvalMachine:
    name: str
    type: str
    days_since_pm: int | None = None


PLANT_NAMES = {"STP-L1-PR01": "250t 伺服沖床", "STP-L1-FD02": "伺服送料機", "STP-L2-PR03": "200t 機械沖床",
               "STP-L2-FD04": "伺服送料機（L2）"}


def _hhmmss(t: str) -> str:
    return t[11:19] if len(t) >= 19 and t[10] == "T" else t


def case_to_incident(case: dict) -> tuple[EvalIncident, EvalMachine, list[str]]:
    """把案例的 trigger／context／log_signals 轉成正式流程看到的 Incident、機台、10 分鐘時間線。

    只用「事件當下看得到」的欄位；不用 final_root_cause、first_hypothesis、處置、回報等事後資訊。
    """
    trig = case.get("trigger") or {}
    ctx = case.get("context") or {}
    mid = case.get("machine_id")
    facts: dict[str, Any] = {"source": trig.get("source", "fused")}
    facts.update(signal_facts(case.get("log_signals") or [], mid))
    if ctx.get("recent_changes"):
        facts["recent_changes"] = ctx["recent_changes"]
    for k in ("material", "die_id", "part", "fixture_id", "program", "tool_id", "station"):
        if ctx.get(k) is not None:
            facts[k] = ctx[k]
    inc = EvalIncident(line=case["line"], type=trig.get("event_type", "unknown"), machine_id=mid,
                       rule_severity=trig.get("rule_severity"), facts=facts, trace_id=case.get("case_id"))
    mach = EvalMachine(name=PLANT_NAMES.get(mid or "", mid or case.get("machine_type", "")),
                       type=case.get("machine_type", ""), days_since_pm=ctx.get("days_since_pm"))
    timeline = []
    for x in sorted(case.get("log_signals") or [], key=lambda x: str(x.get("t"))):
        where = f"（{x['machine_id']}）" if x.get("machine_id") and x.get("machine_id") != mid else ""
        timeline.append(f"{_hhmmss(str(x.get('t')))} {x['signal']}={x.get('value')}{where}")
    return inc, mach, timeline


# ── 正確答案 ───────────────────────────────────────────────────
def expected(case: dict, kb: KnowledgeBase) -> dict[str, Any]:
    """類別、誤報、嚴重度、根因的正確答案。

    嚴重度：誤報 → S1；否則取 max(規則判定, 故障模式 severity_default)。severity_note 的 S3 條件
    在案例產生器裡已經反映到 trigger.rule_severity（例如光幕停用＋人員在危險區、同一氣源多台失壓）。
    """
    fa = case.get("outcome") == "false_alarm" or case.get("final_root_cause") == "none"
    rc = case.get("final_root_cause")
    fm = kb.failure_modes.get(case["line"], {}).get(rc) or {}
    if fa:
        sev = "S1"
    else:
        base = fm.get("severity_default") or "S2"
        if not fm and (case.get("first_hypothesis") or {}).get("failure_mode_id"):
            base = (kb.failure_modes.get(case["line"], {}).get(case["first_hypothesis"]["failure_mode_id"]) or {}
                    ).get("severity_default", "S2")
        rule = (case.get("trigger") or {}).get("rule_severity") or "S1"
        sev = max(base, rule, key=SEV_RANK.get)
    return {"category": "false_alarm" if fa else case.get("category"), "case_category": case.get("category"),
            "is_false_alarm": fa, "severity": sev, "root_cause": rc,
            "rc_in_catalog": rc in kb.failure_modes.get(case["line"], {})}


@dataclass
class EvalItem:
    item_id: str
    line: str
    case: dict
    incident: EvalIncident
    machine: EvalMachine
    timeline: list[str]
    gt: dict[str, Any]
    synthetic: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


def heldout_items(kb: KnowledgeBase) -> list[EvalItem]:
    out = []
    for c in ing.load_cases(KNOWLEDGE, heldout=True):
        inc, m, tl = case_to_incident(c)
        out.append(EvalItem(c["case_id"], c["line"], c, inc, m, tl, expected(c, kb),
                            meta={"case_type": c.get("case_type"), "first_hypothesis": c.get("first_hypothesis")}))
    return out
