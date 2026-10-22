"""信心度分流（程式邏輯，Jev 不做最終決定）＋ J4 安全把關 ＋ J5 結案回寫檢查。"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from . import questions as Q
from .client import JevClient
from .judge import EventJudgement

SEVERITY_RANK = {"S1": 1, "S2": 2, "S3": 3}


def max_severity(*sevs: str | None) -> str:
    return max((s for s in sevs if s), key=lambda s: SEVERITY_RANK.get(s, 0), default="S1")


class GateDecision(BaseModel):
    action: str                       # dispatch／log_only
    severity: str
    category: str | None = None
    diagnosis_mode: str = "rules_only"   # jev_top3／llm_deep／rules_only
    reason: str | None = None


def decide(rule_sev: str | None, j: EventJudgement, *, p_false_alarm: float = 0.9,
           root_cause_conf: float = 0.8) -> GateDecision:
    # 1) 安全底線：只能升級，不能降級（規則判 S3 的事件 Jev 不能降級）
    final_sev = max_severity(rule_sev or "S1", j.jev_severity)
    # 3) 根因信心度
    # 3) 根因信心度：top-1 機率 ≥ 門檻 → 只附 Jev 前三名；不到 → 仍立即派工附前三名，另外啟動 LLM 深入診斷
    #    （eval E3：Jev 在 0.6–0.8 區間過度自信，門檻由 0.6 提高到 0.8）
    top1_p = j.root_cause_top3[0].p if j.root_cause_top3 else 0.0
    diag = "jev_top3" if j.root_cause_top3 and top1_p >= root_cause_conf else "llm_deep"
    # 2) 誤報過濾：只有非 S3、而且 Jev 非常確定是誤報時才壓掉（仍建單、仍記錄，但不通知）
    if final_sev != "S3" and j.p_false_alarm > p_false_alarm:
        return GateDecision(action="log_only", severity=final_sev, category=j.category, diagnosis_mode=diag,
                            reason="jev_false_alarm")
    return GateDecision(action="dispatch", severity=final_sev, category=j.category, diagnosis_mode=diag)


class SafetyGate(BaseModel):
    step: str
    needs_loto: float
    enters_danger_zone: float
    high_voltage: float
    energized: float = 0.0
    flagged: bool
    reasons: list[str] = []
    loto_satisfied: bool = False      # 工單已 LOTO_DONE：needs_loto 只記錄，不單獨要求主管確認
    source: str = "jev"               # jev／keyword（Jev 不可用時的保守退路）／energized_test


DEFAULT_GATE_THRESHOLDS = {"needs_loto": 0.3, "enters_danger_zone": 0.3, "high_voltage": 0.3, "energized": 0.3}


def gate_reasons(p: dict[str, float], thresholds: dict[str, float], loto_done: bool) -> list[str]:
    """哪些題目超過門檻。LOTO 已完成時 needs_loto 視為已滿足。"""
    th = DEFAULT_GATE_THRESHOLDS | (thresholds or {})
    return [k for k, v in p.items() if k in th and v > th[k] and not (k == "needs_loto" and loto_done)]


async def step(jev: JevClient, step_text: str, machine_type: str | None, *, thresholds: dict[str, float] | None = None,
               loto_done: bool = False, trace_id: str | None = None) -> SafetyGate:
    """J4：任一題超過門檻 → flagged（門檻由 eval/gate_set.yaml 實測校準，危險步驟召回率必須 1.0）。"""
    res = await jev.ask({"machine_type": machine_type, "step": step_text}, Q.SAFETY_GATE, purpose="gate.step",
                        trace_id=trace_id, questions_ver=Q.GATE_VER)
    p = {k: res.noul(k) for k in Q.SAFETY_GATE}
    reasons = gate_reasons(p, thresholds or {}, loto_done)
    return SafetyGate(step=step_text, **p, flagged=bool(reasons), reasons=reasons, loto_satisfied=loto_done)


async def steps(jev: JevClient, step_texts: list[str], machine_type: str | None, *,
                thresholds: dict[str, float] | None = None, loto_done: bool = False,
                trace_id: str | None = None) -> list[SafetyGate | BaseException]:
    """多個步驟同時送 J4（asyncio.gather，共用 keep-alive 連線）；順序與輸入相同，失敗的位置是例外物件。

    刻意每步一個請求、不把多步塞進同一個 Jev state：門檻是用「單一步驟」的題目在 gate_set 上校準的
    （recall 1.00），合併題目會改變輸入分佈、需要重新校準。平行送出的總時間 ≈ 最慢的一個請求。
    """
    return list(await asyncio.gather(*(step(jev, t, machine_type, thresholds=thresholds, loto_done=loto_done,
                                            trace_id=trace_id) for t in step_texts), return_exceptions=True))


class WritebackCheck(BaseModel):
    complete: float
    root_cause: str | None
    root_cause_conf: float = 0.0
    ok: bool
    missing: list[str] = []
    new_failure_mode: bool = False


async def writeback(jev: JevClient, report: str, line: str, failure_modes: dict[str, dict], *,
                    threshold: float = 0.7, trace_id: str | None = None) -> WritebackCheck:
    """J5：complete < threshold → 退回補寫；root_cause = new → 通知工程師新增故障模式。"""
    criteria = {fid: fm.get("criteria") or fm.get("label") or fid for fid, fm in failure_modes.items()}
    res = await jev.ask({"line": line, "report": report}, Q.writeback_questions(criteria), purpose="gate.writeback",
                        trace_id=trace_id, questions_ver=Q.WRITEBACK_VER)
    rc = res.choice("root_cause")
    complete = res.noul("complete")
    missing = [label for k, label in Q.WRITEBACK_PARTS.items() if k in res.answers and res.noul(k) < 0.5]
    if complete < threshold and not missing:
        missing = list(Q.WRITEBACK_PARTS.values())
    return WritebackCheck(complete=complete, root_cause=rc.choice, root_cause_conf=rc.confidence,
                          ok=complete >= threshold, missing=missing if complete < threshold else [],
                          new_failure_mode=rc.choice == "new")
