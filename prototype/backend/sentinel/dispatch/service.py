"""派工流程：alerts（新 Incident）→ 分流 → Jev J1–J3 → 信心度分流 → 建單 → 指派 → 通知 → LLM 說明補發。

事件發生到通知送出不等 LLM；Jev 逾時或錯誤時照規則派工，通知註明「AI 判斷暫不可用」。
"""
from __future__ import annotations

import logging
from sqlmodel import col, select

from ..audit.log import AuditLogger
from ..bus import EventBus
from ..clock import Clock
from ..config import Settings
from ..db.models import Diagnosis, Incident, Notification, Person, Workorder
from ..db.session import Database
from ..jev import gate, judge
from ..jev.client import JevClient, JevUnavailable
from ..kb.retrieve import KnowledgeBase
from ..llm import explain
from ..llm.client import LlmClient, LlmUnavailable
from ..rules.engine import TYPE_LABEL
from ..rules.routing import Plan, route
from ..rules.windows import Windows
from ..schemas.events import Event, Topic
from ..schemas.plant import Plant
from ..sim.roster import ROLE_LABEL, Roster
from .notifier import CAT_LABEL, Message, Notifier
from .workorder import WorkorderService

log = logging.getLogger(__name__)



class DispatchService:
    def __init__(self, *, db: Database, bus: EventBus, clock: Clock, audit: AuditLogger, settings: Settings,
                 plant: Plant, roster: Roster, kb: KnowledgeBase, jev: JevClient, llm: LlmClient,
                 notifier: Notifier, workorders: WorkorderService, windows: Windows):
        self.db, self.bus, self.clock, self.audit, self.settings = db, bus, clock, audit, settings
        self.plant, self.roster, self.kb, self.jev, self.llm = plant, roster, kb, jev, llm
        self.notifier, self.workorders, self.windows = notifier, workorders, windows
        self.th = settings.thresholds
        self._message_ids: dict[str, str] = {}   # workorder_id → 派工訊息 ID（說明以回覆補上）

    def start(self) -> None:
        self.bus.consume(Topic.ALERTS, "dispatch", self.on_alert)

    async def on_alert(self, evt: Event) -> None:
        if evt.type != "incident_opened":
            return
        inc = self.db.get(Incident, evt.payload["incident_id"])
        if inc is not None:
            await self.handle_incident(inc)

    # ── 主流程 ─────────────────────────────────────────────────
    async def handle_incident(self, inc: Incident) -> Workorder:
        now = self.clock.now()
        machine = self.plant.machines.get(inc.machine_id or "")
        plan = route(inc, self.roster, now)
        self.audit.record("routed", trace_id=inc.trace_id, incident_id=inc.id, plan=plan.as_dict())
        diag = Diagnosis(incident_id=inc.id, mode="rules_only")
        ai_note: str | None = None
        judgement = None

        if plan.ai:
            fms = self.kb.failure_modes_for(inc.line, machine.type if machine else None)
            cases = self.kb.similar_cases(inc, k=5, machine_type=machine.type if machine else None)
            try:
                judgement = await judge.event(self.jev, inc, machine=machine, similar_cases=cases, failure_modes=fms,
                                              severity_notes=self.kb.severity_notes(inc.line, machine.type if machine else None),
                                              timeline=list(self.windows.timeline.get(inc.machine_id or inc.camera_id or "", [])))
            except JevUnavailable as e:
                ai_note = "AI 判斷暫不可用"
                self.audit.record("jev_unavailable", actor="jev", trace_id=inc.trace_id, error=str(e))
            if judgement is not None:
                decision = gate.decide(inc.rule_severity, judgement, p_false_alarm=self.th.p_false_alarm,
                                       root_cause_conf=self.th.root_cause_conf)
                top3 = [r.model_dump() for r in judgement.root_cause_top3]
                sop_plan = self.kb.plan_sops(top3, top1_thr=self.th.sop_top1, low_thr=self.th.sop_low)
                diag = Diagnosis(incident_id=inc.id, root_cause_top3=top3, confidence=judgement.root_cause_conf,
                                 mode=decision.diagnosis_mode, category=judgement.category,
                                 p_false_alarm=judgement.p_false_alarm, jev_severity=judgement.jev_severity,
                                 sop_ids=sorted({c.sop_id for c in sop_plan["chunks"]}),
                                 similar_cases=[c.model_dump(exclude={"short"}) for c in cases])
                self.audit.record("jev_judgement", actor="jev", trace_id=inc.trace_id,
                                  judgement=judgement.model_dump(), decision=decision.model_dump(),
                                  similar_case_ids=[c.case_id for c in cases])
                with self.db.session() as s:
                    row = s.get(Incident, inc.id)
                    assert row is not None
                    row.final_severity = decision.severity
                    row.suspected_false_alarm = decision.action == "log_only"
                    s.add(row)
                    s.commit()
                    s.refresh(row)
                    inc = row
                if decision.severity != plan_severity(plan, inc):
                    plan = route(inc, self.roster, now)           # Jev 升級 → 重新路由（例如 S3 → 緊急）
                if decision.action == "log_only":
                    plan.mode = "log_only"
        self.db.add(diag)
        await self.bus.publish(Event(ts=self.clock.now(), topic=Topic.AI, source="jev", line=inc.line,
                                     machine_id=inc.machine_id, camera_id=inc.camera_id, type="diagnosis",
                                     trace_id=inc.trace_id, payload={
                                         "incident_id": inc.id, "root_cause_top3": diag.root_cause_top3,
                                         "confidence": diag.confidence, "diagnosis_mode": diag.mode,
                                         "final_severity": inc.final_severity, "ai_note": ai_note,
                                         "suspected_false_alarm": inc.suspected_false_alarm,
                                         "sop_ids": diag.sop_ids}))

        wo = await self.workorders.create(inc, severity=inc.final_severity, category=inc.category)
        if plan.mode == "log_only":
            self.audit.record("notify_suppressed", trace_id=inc.trace_id, workorder_id=wo.id,
                              reason="疑似誤報" if inc.suspected_false_alarm else "S1 記錄即可")
            return wo
        if plan.assignee_id:
            wo = await self.workorders.assign(
                wo.id, person_id=plan.assignee_id, role=plan.assignee_role, cc=plan.cc,
                ack_timeout_s=self.settings.escalation.timeout_for(inc.final_severity),
                reason="；".join(plan.notes) or f"指派{ROLE_LABEL.get(plan.assignee_role or '', '')}")
        await self.notify(wo, inc, diag, plan, kind="dispatch", ai_note=ai_note)
        if plan.ai:
            self.bus.track(self._explain(inc, diag, wo), name=f"llm.explain:{inc.id}")
        return wo

    # ── 通知 ───────────────────────────────────────────────────
    def _person(self, pid: str | None) -> Person | None:
        return self.db.get(Person, pid) if pid else None

    def compose(self, wo: Workorder, inc: Incident, diag: Diagnosis | None, *, kind: str,
                ai_note: str | None = None, extra: list[str] | None = None) -> Message:
        machine = self.plant.machines.get(inc.machine_id or "")
        where = f"{inc.machine_id} {machine.name}" if machine else (inc.camera_id or inc.machine_id or "")
        title = (f"【{inc.final_severity}｜{CAT_LABEL.get(inc.category, inc.category)}】{where}："
                 f"{TYPE_LABEL.get(inc.type, inc.type)}")
        p = self._person(wo.assignee_id)
        lines = [f"工單 {wo.id}（{inc.id}）→ 指派：{ROLE_LABEL.get(wo.assignee_role or '', '')} {p.name if p else '未指派'}"]
        if kind == "escalation":
            lines.insert(0, f"⚠ 逾時未接單，第 {wo.escalation_level} 次升級")
        if wo.cc:
            names = [x.name for x in (self._person(c) for c in wo.cc) if x]
            lines.append(f"副本：{'、'.join(names)}")
        if diag and diag.root_cause_top3:
            lines.append("根因前三名：" + "｜".join(f"{r.get('label', r['id'])} {r['p']:.0%}" for r in diag.root_cause_top3))
        if diag and diag.sop_ids:
            lines.append("SOP：" + "、".join(diag.sop_ids))
        if inc.category == "safety":
            lines.append("處置：請班長到場確認並提醒配戴 PPE（提醒，不懲罰）")
        if ai_note:
            lines.append(f"註：{ai_note}")
        lines.extend(extra or [])
        lines.append(f"工單連結：/workorders/{wo.id}")
        return Message(channel="emergency" if inc.final_severity == "S3" else inc.line, title=title, lines=lines,
                       severity=inc.final_severity, workorder_id=wo.id, recipient_id=wo.assignee_id)

    async def notify(self, wo: Workorder, inc: Incident, diag: Diagnosis | None, plan: Plan | None, *,
                     kind: str, ai_note: str | None = None, extra: list[str] | None = None) -> str:
        msg = self.compose(wo, inc, diag, kind=kind, ai_note=ai_note, extra=extra)
        return await self._send(wo, inc, msg, kind)

    async def _send(self, wo: Workorder, inc: Incident, msg: Message, kind: str) -> str:
        mid = await self.notifier.send(msg)
        if kind in ("dispatch", "escalation"):
            self._message_ids[wo.id] = mid
        self.db.add(Notification(workorder_id=wo.id, channel=msg.channel, recipient_id=msg.recipient_id,
                                 discord_message_id=mid, kind=kind, text=msg.text, ts=self.clock.now()))
        self.audit.record("notified", trace_id=inc.trace_id, workorder_id=wo.id, kind=kind, channel=msg.channel,
                          notifier=self.notifier.name, recipient_id=msg.recipient_id)
        await self.bus.publish(Event(ts=self.clock.now(), topic=Topic.TASKS, source="dispatch.notifier",
                                     line=inc.line, machine_id=inc.machine_id, camera_id=inc.camera_id,
                                     type="notification_sent", trace_id=inc.trace_id,
                                     payload={"workorder_id": wo.id, "kind": kind, "channel": msg.channel,
                                              "text": msg.text}))
        return mid

    async def _explain(self, inc: Incident, diag: Diagnosis, wo: Workorder) -> None:
        machine = self.plant.machines.get(inc.machine_id or "")
        sops = self.kb.plan_sops(diag.root_cause_top3, top1_thr=self.th.sop_top1, low_thr=self.th.sop_low)["chunks"]
        try:
            if diag.mode == "llm_deep":       # Jev 根因信心不足：LLM 讀時間線與相似案例深入診斷
                cases = self.kb.similar_cases(inc, k=5, machine_type=machine.type if machine else None)
                text = await explain.deep(self.llm, inc, diag.root_cause_top3, sops, machine.name if machine else None,
                                          timeline=list(self.windows.timeline.get(inc.machine_id or inc.camera_id or "", [])),
                                          cases=[c.short for c in cases])
            else:
                text = await explain.diagnosis(self.llm, inc, diag.root_cause_top3, sops,
                                               machine.name if machine else None)
            ok = True
        except LlmUnavailable as e:
            text, ok = explain.FALLBACK, False
            self.audit.record("llm_unavailable", actor="llm", trace_id=inc.trace_id, error=str(e))
        with self.db.session() as s:
            row = s.get(Diagnosis, diag.id)
            if row:
                row.explanation = text
                s.add(row)
                s.commit()
        self.audit.record("llm_explanation", actor="llm", trace_id=inc.trace_id, ok=ok, chars=len(text))
        await self.bus.publish(Event(ts=self.clock.now(), topic=Topic.AI, source="llm", line=inc.line,
                                     machine_id=inc.machine_id, camera_id=inc.camera_id, type="llm_explanation",
                                     trace_id=inc.trace_id, payload={"incident_id": inc.id, "workorder_id": wo.id,
                                                                     "text": text, "ok": ok}))
        msg = Message(channel="emergency" if inc.final_severity == "S3" else inc.line, title=f"💬 {wo.id} 診斷說明",
                      lines=[text], severity=inc.final_severity, workorder_id=wo.id, buttons=[],
                      reply_to=self._message_ids.get(wo.id))
        await self._send(wo, inc, msg, "explanation")

    async def request_supervisor(self, wo_id: str, reason: str, *, actor: str = "system") -> None:
        wo = self.workorders.get(wo_id)
        inc = self.workorders.incident(wo)
        people = self.roster.on_shift(inc.line, self.clock.now())
        sup = next((p for p in people if p.role == "supervisor"), None) or \
            next((p for p in people if p.role == "leader"), None)
        self.audit.record("supervisor_requested", actor=actor, trace_id=inc.trace_id, workorder_id=wo_id,
                          reason=reason, to=sup.id if sup else None)
        msg = Message(channel=inc.line, title=f"🔴 {wo_id} 需要主管確認", lines=[reason, f"工單連結：/workorders/{wo_id}"],
                      severity=inc.final_severity, workorder_id=wo_id, recipient_id=sup.id if sup else None,
                      buttons=[], reply_to=self._message_ids.get(wo_id))
        await self._send(wo, inc, msg, "supervisor")

    def diagnosis_for(self, incident_id: str) -> Diagnosis | None:
        return self.db.first(select(Diagnosis).where(Diagnosis.incident_id == incident_id)
                             .order_by(col(Diagnosis.id).desc()))


def plan_severity(plan: Plan, inc: Incident) -> str:
    """路由當下依據的嚴重度（emergency ⇔ S3）。"""
    return "S3" if plan.mode == "emergency" else ("S1" if plan.mode == "log_only" else "S2")
