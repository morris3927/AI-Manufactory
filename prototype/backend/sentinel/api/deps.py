"""API 共用工具：Runtime 取得、操作人員身分解析。

Demo 不做登入（§8.3）：前端送 X-Role／X-Person-Id。前端的身分 ID（P-T01 等）不在 roster 裡時，
依角色對應到「該產線當班、該職位」的人員，讓操作紀錄仍是真實的 roster ID。
judge（評審）是唯讀身分，所有 POST 一律拒絕（main.py 的 middleware）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from ..sim.roster import ROLE_RANK

if TYPE_CHECKING:
    from ..runtime import Runtime

ROLE_ALIAS = {"technician": "technician", "leader": "leader", "supervisor": "supervisor", "manager": "manager",
              "operator": "operator"}


def get_rt(request: Request) -> "Runtime":
    return request.app.state.rt


def resolve_actor(request: Request, body_person_id: str | None = None, line: str | None = None) -> str:
    rt = get_rt(request)
    role = (request.headers.get("X-Role") or request.query_params.get("role") or "").lower()
    if role == "judge":
        raise HTTPException(403, "評審身分只能檢視，不能操作")
    pid = body_person_id or request.headers.get("X-Person-Id") or request.query_params.get("person_id")
    if pid and rt.roster.person(pid):
        return pid
    r = ROLE_ALIAS.get(role)
    if r:
        lines = [line] if line else [l.id for l in rt.plant.lines]
        for ln in lines:
            for p in rt.roster.on_shift(ln, rt.clock.now()):
                if p.role == r:
                    return p.id
        # 當班沒有這個職位 → 取職位更高的人（例如夜班沒有課長）
        for ln in lines:
            people = [p for p in rt.roster.on_shift(ln, rt.clock.now()) if ROLE_RANK.get(p.role, 0) >= ROLE_RANK[r]]
            if people:
                return min(people, key=lambda p: ROLE_RANK.get(p.role, 0)).id
    raise HTTPException(422, "無法識別操作人員：請帶 person_id，或 X-Role／X-Person-Id")


def base_url(request: Request) -> str:
    return str(request.base_url)
