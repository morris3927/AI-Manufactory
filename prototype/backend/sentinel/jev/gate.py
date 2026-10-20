"""信心度分流（程式邏輯，Jev 不做最終決定）＋ J4 安全把關 ＋ J5 結案回寫檢查。"""
from __future__ import annotations

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
           root_cause_conf: float = 0.6) -> GateDecision:
    # 1) 安全底線：只能升級，不能降級（規則判 S3 的事件 Jev 不能降級）
    final_sev = max_severity(rule_sev or "S1", j.jev_severity)
    # 3) 根因信心度
    diag = "jev_top3" if j.root_cause_top3 and j.root_cause_conf >= root_cause_conf else "llm_deep"
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
    flagged: bool
    source: str = "jev"               # jev／keyword（Jev 不可用時的保守退路）


async def step(jev: JevClient, step_text: str, machine_type: str | None, *, threshold: float = 0.3,
               trace_id: str | None = None) -> SafetyGate:
    """J4：任一機率 > threshold → flagged（故意設低：寧可多問一次，不能漏掉）。"""
    res = await jev.ask({"machine_type": machine_type, "step": step_text}, Q.SAFETY_GATE, purpose="gate.step",
                        trace_id=trace_id, questions_ver=Q.GATE_VER)
    p = {k: res.noul(k) for k in Q.SAFETY_GATE}
    return SafetyGate(step=step_text, **p, flagged=any(v > threshold for v in p.values()))


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
