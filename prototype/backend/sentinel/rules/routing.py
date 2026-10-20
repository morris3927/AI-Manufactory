"""分流與派工路由（RU-3、RU-4）。

1) 事件類別 × 嚴重度：
   safety：S2 → dispatch_only；S3 → emergency
   equipment／quality：S2 → dispatch ＋ ai；S3 → emergency ＋ ai
   S1 → log_only（記錄即可）
2) 指派對象：查當班 roster → 依職位挑人 → 門禁確認在廠 → 不在就改派同職位或上一級
   S2 工安 → 班長；S2 設備 → 當班技術員（副本給班長）；
   S3 → 該線當班職位最高者 ＋ 技術員 ＋ 主管群組（#緊急）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..db.models import Person
from ..sim.roster import ROLE_LABEL, ROLE_RANK, Roster

ESCALATION_LADDER = ["technician", "leader", "supervisor", "manager"]


@dataclass
class Plan:
    mode: str                         # dispatch_only／dispatch／emergency／log_only
    ai: bool
    channel: str                      # 產線頻道或 emergency
    assignee_id: str | None = None
    assignee_role: str | None = None
    cc: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def next_role(role: str | None) -> str | None:
    if role not in ESCALATION_LADDER:
        return ESCALATION_LADDER[0]
    i = ESCALATION_LADDER.index(role)
    return ESCALATION_LADDER[i + 1] if i + 1 < len(ESCALATION_LADDER) else None


def pick(roster: Roster, people: list[Person], role: str, at: datetime, notes: list[str],
         exclude: set[str] = frozenset()) -> Person | None:
    """依職位挑人；門禁不在廠就換同職位下一位，都不在就往上一級。"""
    r: str | None = role
    while r:
        for p in (p for p in people if p.role == r and p.id not in exclude):
            if roster.in_plant(p.id, at):
                return p
            notes.append(f"改派：{ROLE_LABEL.get(r, r)} {p.name} 門禁顯示不在廠")
        r = next_role(r) if r in ESCALATION_LADDER else None
        if r:
            notes.append(f"當班沒有可派的人員，改派{ROLE_LABEL.get(r, r)}")
    return None


def route(incident: Any, roster: Roster, at: datetime) -> Plan:
    cat, sev, line = incident.category, incident.final_severity, incident.line
    people = roster.on_shift(line, at)
    notes: list[str] = []
    if sev == "S1":
        return Plan(mode="log_only", ai=cat != "safety", channel=line)

    if sev == "S3":
        plan = Plan(mode="emergency", ai=cat != "safety", channel="emergency")
        present = [p for p in people if p.role != "operator" and roster.in_plant(p.id, at)]
        top = max(present, key=lambda p: ROLE_RANK.get(p.role, 0), default=None)
        if top:
            plan.assignee_id, plan.assignee_role = top.id, top.role
        plan.cc = [p.id for p in people if p.role in {"technician", "supervisor"} and p.id != plan.assignee_id]
        plan.notes = notes
        return plan

    if cat == "safety":
        plan = Plan(mode="dispatch_only", ai=False, channel=line)
        primary = pick(roster, people, "leader", at, notes)
    else:
        plan = Plan(mode="dispatch", ai=True, channel=line)
        primary = pick(roster, people, "technician", at, notes)
        leader = pick(roster, people, "leader", at, [], exclude={primary.id} if primary else set())
        if leader:
            plan.cc = [leader.id]
    if primary:
        plan.assignee_id, plan.assignee_role = primary.id, primary.role
    else:
        notes.append("找不到可指派的當班人員")
    plan.notes = notes
    return plan
