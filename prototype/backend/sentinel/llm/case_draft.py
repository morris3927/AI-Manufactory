"""CL-3：結案回報 → LLM 整理成案例草稿欄位（摘要、原因、處置步驟、驗證結果、注意事項）。

只整理技術員寫的內容，不新增事實或數值：
- 提示詞（agent/prompts/case_draft_system.md）明寫規則；
- 程式再檢查一次：輸出裡的每個數字都要在提供給 LLM 的資料裡出現過，否則整份不採用（退回程式組的草稿）。
輸出欄位沿用知識庫案例格式：technician_report_normalized（摘要）、discriminating_evidence（原因）、
actions（處置步驟，最後一項是驗證）、lessons（注意事項）、why_first_guess_wrong。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .client import LlmClient, LlmResult

SYSTEM = (Path(__file__).resolve().parents[1] / "agent" / "prompts" / "case_draft_system.md").read_text(encoding="utf-8")
PROMPT_VER = "case-draft-v1"

FIELDS = ("technician_report_normalized", "discriminating_evidence", "actions", "lessons", "why_first_guess_wrong")
MAX_TEXT = 400
MAX_ITEMS = 12
NUM = re.compile(r"\d+(?:\.\d+)?")
# 給現場看的字：不寫 LOTO、零能量（提示詞已要求，這裡再保險一次）
TERMS = ((re.compile(r"\s*驗證零能量\s*"), "確認機台不會動"), (re.compile(r"\s*零能量\s*"), "機台不會動"),
         (re.compile(r"\s*\bLOTO\b(?:-[A-Z0-9]+)*\s*"), "上鎖掛牌"))


class CaseDraftInvalid(ValueError):
    """LLM 回覆無法解析，或加了資料裡沒有的數值。"""


def build_messages(*, wo_id: str, inc: Any, machine_name: str | None, report: str, final_rc: str | None,
                   rc_label: str | None, diag: Any, labels: dict[str, str]) -> list[dict]:
    facts = "\n".join(f"- {k}: {v}" for k, v in (inc.facts or {}).items() if v is not None and k != "source") or "- 無"
    top3 = (diag.root_cause_top3 or []) if diag else []
    rc = "\n".join(f"- {labels.get(r.get('id'), r.get('label') or r.get('id'))}：{r.get('p', 0):.0%}" for r in top3) or "- 無"
    first = labels.get(top3[0].get("id"), top3[0].get("id")) if top3 else None
    user = (f"工單：{wo_id}\n機台：{machine_name or inc.machine_id or inc.camera_id}（{inc.line}）\n"
            f"事件：{inc.type}，嚴重度 {inc.final_severity}\n事件事實：\n{facts}\n"
            f"Jev 根因機率（第一判斷：{first or '無'}）：\n{rc}\n"
            f"Jev 診斷說明：{(diag.explanation if diag else None) or '無'}\n"
            f"技術員確認的根因：{rc_label or final_rc or '未填'}\n"
            f"技術員結案回報（原文）：\n{report}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _json_object(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise CaseDraftInvalid("回覆不是 JSON")
    try:
        d = json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise CaseDraftInvalid(f"JSON 解析失敗：{e.msg}") from e
    if not isinstance(d, dict):
        raise CaseDraftInvalid("回覆不是 JSON 物件")
    return d


def _clean(s: Any) -> str:
    s = str(s or "").strip()
    for pat, repl in TERMS:
        s = pat.sub(repl, s)
    return s[:MAX_TEXT]


def _numbers(text: str) -> set[float]:
    return {float(x) for x in NUM.findall(text)}


def parse(text: str, source: str) -> dict[str, Any]:
    """LLM 回覆 → 案例欄位；格式不對或出現資料裡沒有的數值 → CaseDraftInvalid。"""
    d = _json_object(text)
    summary = _clean(d.get("technician_report_normalized"))
    if not summary:
        raise CaseDraftInvalid("缺少摘要（technician_report_normalized）")
    acts = d.get("actions")
    if not isinstance(acts, list) or not acts:
        raise CaseDraftInvalid("缺少處置步驟（actions）")
    actions = []
    for a in acts[:MAX_ITEMS]:
        if isinstance(a, str):
            a = {"step": a, "result": ""}
        if not isinstance(a, dict) or not _clean(a.get("step")):
            raise CaseDraftInvalid("處置步驟格式不對")
        actions.append({"step": _clean(a.get("step")), "result": _clean(a.get("result"))})
    ev = d.get("discriminating_evidence") or []
    if isinstance(ev, str):
        ev = [ev]
    if not isinstance(ev, list):
        raise CaseDraftInvalid("原因（discriminating_evidence）格式不對")
    out = {"technician_report_normalized": summary,
           "discriminating_evidence": [_clean(x) for x in ev[:MAX_ITEMS] if _clean(x)],
           "actions": actions, "lessons": _clean(d.get("lessons")),
           "why_first_guess_wrong": _clean(d.get("why_first_guess_wrong")) or None}
    allowed = _numbers(source)
    written = " ".join([out["technician_report_normalized"], *out["discriminating_evidence"], out["lessons"],
                        out["why_first_guess_wrong"] or ""] + [f"{a['step']} {a['result']}" for a in actions])
    extra = sorted(_numbers(written) - allowed)
    if extra:
        raise CaseDraftInvalid("出現資料裡沒有的數值：" + "、".join(f"{x:g}" for x in extra[:5]))
    return out


async def draft(llm: LlmClient, messages: list[dict], *, trace_id: str | None) -> tuple[dict[str, Any], LlmResult]:
    """呼叫 LLM 並解析；LLM 不可用 → LlmUnavailable，回覆不合格 → CaseDraftInvalid。"""
    res = await llm.chat(messages, purpose="kb.case_draft", trace_id=trace_id, options={"num_predict": 1200},
                         think=False)
    return parse(res.content, "\n".join(m["content"] for m in messages if m["role"] == "user")), res
