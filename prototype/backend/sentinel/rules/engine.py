"""規則引擎（RU-1、RU-2、RU-6）。

- `@rule(...)` 註冊規則；每條規則有固定 ID，命中時寫入稽核紀錄（GV-2）。
- `facts` 是程式先算好的結論，直接放進 Jev 的 state（Jev 不擅長算數）。
- Incident 去重：同一主體（機台／攝影機）、同類型、還沒結案 → 更新原 Incident，不重開。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlmodel import select

from ..audit.log import AuditLogger
from ..bus import EventBus
from ..clock import Clock
from ..config import Thresholds
from ..db.models import Incident
from ..db.session import Database
from ..ids import jsonable, new_id
from ..schemas.events import Event, Topic
from ..schemas.plant import Plant
from .windows import Windows

log = logging.getLogger(__name__)

TYPE_LABEL = {"feed_jam": "送料卡料", "ppe_violation": "未戴安全帽", "log_mismatch": "影像與 Log 狀態不一致"}


@dataclass
class Finding:
    type: str
    facts: dict[str, Any] = field(default_factory=dict)
    severity: str | None = None          # 不填 → 規則預設嚴重度


RuleFn = Callable[[Windows, str], Finding | None]


@dataclass
class RuleSpec:
    id: str
    line: str                            # 產線 ID；"*" 表示所有產線
    category: str                        # safety／equipment／quality
    default_severity: str
    fn: RuleFn
    machine_types: tuple[str, ...] = ()  # 空 → 不限機型
    entity_kind: str = "machine"         # machine／camera

    def applies(self, line: str | None, entity: str, plant: Plant) -> bool:
        if self.line != "*" and self.line != line:
            return False
        if self.entity_kind == "camera":
            return entity in plant.cameras
        m = plant.machines.get(entity)
        if m is None:
            return False
        return not self.machine_types or m.type in self.machine_types


REGISTRY: dict[str, RuleSpec] = {}


def rule(*, id: str, line: str, category: str, default_severity: str,
         machine_types: tuple[str, ...] | list[str] = (), entity_kind: str = "machine") -> Callable[[RuleFn], RuleFn]:
    def deco(fn: RuleFn) -> RuleFn:
        REGISTRY[id] = RuleSpec(id=id, line=line, category=category, default_severity=default_severity, fn=fn,
                                machine_types=tuple(machine_types), entity_kind=entity_kind)
        return fn

    return deco


def load_rules() -> dict[str, RuleSpec]:
    from . import rules_assembly, rules_cross, rules_stamping  # noqa: F401  匯入即註冊

    return REGISTRY


class RuleEngine:
    def __init__(self, plant: Plant, windows: Windows, rules: dict[str, RuleSpec] | None = None):
        self.plant = plant
        self.windows = windows
        self.rules = rules if rules is not None else load_rules()

    def evaluate(self, entity: str) -> list[tuple[RuleSpec, Finding]]:
        line = self.plant.line_of(entity)
        hits: list[tuple[RuleSpec, Finding]] = []
        for spec in self.rules.values():
            if not spec.applies(line, entity, self.plant):
                continue
            try:
                f = spec.fn(self.windows, entity)
            except Exception:
                log.exception("規則 %s 執行失敗", spec.id)
                continue
            if f is not None:
                hits.append((spec, f))
        return hits


class IncidentManager:
    def __init__(self, *, db: Database, bus: EventBus, clock: Clock, audit: AuditLogger, plant: Plant,
                 thresholds: Thresholds):
        self.db, self.bus, self.clock, self.audit, self.plant, self.th = db, bus, clock, audit, plant, thresholds
        self._last_pub: dict[str, Any] = {}
        self.on_opened: Callable[[Incident], None] | None = None   # 新 Incident 成立時（例如存關鍵幀）

    def _next_id(self) -> str:
        with self.db.session() as s:
            n = len(s.exec(select(Incident.id)).all())
        return f"INC-{self.clock.now():%Y%m%d}-{n + 1:04d}"

    def find_open(self, entity: str, type_: str) -> Incident | None:
        with self.db.session() as s:
            return s.exec(select(Incident).where(
                (Incident.machine_id == entity) | (Incident.camera_id == entity),
                Incident.type == type_, Incident.status == "open")).first()

    async def on_finding(self, spec: RuleSpec, finding: Finding, entity: str) -> Incident:
        now = self.clock.now()
        existing = self.find_open(entity, finding.type)
        if existing:                                         # RU-6 去重：只更新
            with self.db.session() as s:
                inc = s.get(Incident, existing.id)
                assert inc is not None
                inc.facts = jsonable({**inc.facts, **finding.facts})
                inc.last_seen_at = now
                s.add(inc)
                s.commit()
                s.refresh(inc)
            last = self._last_pub.get(inc.id)
            if last is None or (now - last).total_seconds() >= self.th.incident_update_s:
                self._last_pub[inc.id] = now
                await self.bus.publish(self._event(inc, "incident_updated"))
            return inc
        is_machine = entity in self.plant.machines
        sev = finding.severity or spec.default_severity
        inc = Incident(
            id=self._next_id(), trace_id=new_id(now), line=self.plant.line_of(entity) or spec.line,
            machine_id=entity if is_machine else None,
            camera_id=(next((c.id for c in self.plant.cameras_for_machine(entity)), None) if is_machine else entity), type=finding.type, category=spec.category,
            rule_id=spec.id, rule_severity=sev, final_severity=sev,
            facts=jsonable({**finding.facts, "detect_latency_s": finding.facts.get("seconds_since_drop")}),
            opened_at=now, last_seen_at=now,
        )
        self.db.add(inc)
        self._last_pub[inc.id] = now
        self.audit.record("rule_hit", trace_id=inc.trace_id, rule_id=spec.id, incident_id=inc.id,
                          entity=entity, severity=sev, facts=finding.facts)
        log.info("規則 %s 命中 → %s（%s %s）", spec.id, inc.id, finding.type, sev)
        if self.on_opened:
            try:
                self.on_opened(inc)
            except Exception:
                log.exception("on_opened 失敗")
        await self.bus.publish(self._event(inc, "incident_opened"))
        return inc

    def _event(self, inc: Incident, type_: str) -> Event:
        return Event(ts=self.clock.now(), topic=Topic.ALERTS, source="rules", line=inc.line,
                     machine_id=inc.machine_id, camera_id=inc.camera_id, type=type_, trace_id=inc.trace_id,
                     payload={"incident_id": inc.id, "type": inc.type, "category": inc.category,
                              "rule_id": inc.rule_id, "rule_severity": inc.rule_severity,
                              "final_severity": inc.final_severity, "severity": inc.final_severity,
                              "facts": inc.facts, "status": inc.status, "title": self.title(inc)})

    def title(self, inc: Incident) -> str:
        m = self.plant.machines.get(inc.machine_id or "")
        where = f"{m.id} {m.name}" if m else (inc.camera_id or "")
        return f"{where}：{TYPE_LABEL.get(inc.type, inc.type)}"
