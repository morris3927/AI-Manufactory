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
