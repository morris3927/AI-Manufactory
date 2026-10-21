"""LM-1：把 Jev 的根因機率與 SOP 寫成給技術員看的說明（非同步，派工後補上）。"""
from __future__ import annotations

from typing import Any

from .client import LlmClient

SYSTEM = (
    "你是工廠維修主管的助理，用繁體中文（台灣用語）寫給現場技術員看的簡短診斷說明。"
    "規則：1) 只根據提供的事實、根因機率與 SOP，不要編造數據；2) 先一句話講發生什麼事；"
    "3) 依機率說明最可能的根因與要先檢查什麼（能最快排除的先查）；4) 一定提醒先依 LOTO 上鎖掛牌；"
    "5) 不可以說「可以復電」或「解除上鎖」；6) 150 字以內，不用 Markdown 標題。"
)

FALLBACK = "說明產生中斷，請依工單附的 SOP 與 LOTO 清單處理。"


def build_messages(incident: Any, top3: list[dict], sops: list[Any], machine_name: str | None) -> list[dict]:
    facts = "\n".join(f"- {k}: {v}" for k, v in (incident.facts or {}).items())
    rc = "\n".join(f"- {r.get('label', r.get('id'))}（{r.get('id')}）：{r.get('p', 0):.0%}" for r in top3) or "- 無"
    sop_text = "\n".join(f"[{getattr(s, 'sop_id', '')} {getattr(s, 'phase', '')}] {getattr(s, 'text', s)}"
                         for s in sops[:4]) or "（沒有對應 SOP）"
    user = (f"機台：{machine_name or incident.machine_id or incident.camera_id}（{incident.line}）\n"
            f"事件：{incident.type}，嚴重度 {incident.final_severity}\n事實：\n{facts}\n"
            f"根因機率（Jev）：\n{rc}\nSOP 摘錄：\n{sop_text}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


async def diagnosis(llm: LlmClient, incident: Any, top3: list[dict], sops: list[Any],
                    machine_name: str | None = None) -> str:
    res = await llm.chat(build_messages(incident, top3, sops, machine_name), purpose="explain.diagnosis",
                         trace_id=incident.trace_id)
    return res.content.strip() or FALLBACK


DEEP_SYSTEM = (
    "你是工廠維修工程師，用繁體中文（台灣用語）做深入診斷。Jev 的根因機率信心不足（top-1 < 0.8），"
    "請根據時間線、事實與相似案例（含第一判斷錯誤的案例）重新評估：1) 逐一說明前三名根因的支持與反對證據；"
    "2) 指出最能區分它們的一項檢查（先做這一項）；3) 若都不符合，明說「可能是目錄外的新故障模式」。"
    "一定提醒先依作業許可與 LOTO 上鎖；不可以說「可以復電」。250 字以內，不用 Markdown 標題。"
)


async def deep(llm: LlmClient, incident: Any, top3: list[dict], sops: list[Any], machine_name: str | None = None, *,
               timeline: list[str], cases: list[str]) -> str:
    """LM-2：Jev 信心不足時的深入診斷（派工已先送出，這段非同步補上）。"""
    msgs = build_messages(incident, top3, sops, machine_name)
    msgs[0] = {"role": "system", "content": DEEP_SYSTEM}
    msgs[1]["content"] += ("\n時間線（近 10 分鐘）：\n" + "\n".join(timeline[-15:]) +
                           "\n相似案例：\n" + "\n".join(f"- {c}" for c in cases[:5]))
    res = await llm.chat(msgs, purpose="explain.deep", trace_id=incident.trace_id)
    return res.content.strip() or FALLBACK
