"""S3 職災通報（GV-4；職業安全衛生法第37條第2項）。

只有人（主管在介面標記）或劇本腳本設定的傷亡資料符合下列任一項才觸發，系統不會從影像推論傷亡：
  死亡 ≥ 1、罹災 ≥ 3、罹災需住院 ≥ 1（「其他經中央主管機關指定公告」由主管判斷時勾選 designated）。
觸發後：建立通報草稿、8 小時倒數、顯示「非經司法機關或檢查機構許可，不得移動或破壞現場」、
通知職安人員＋急救人員＋該線當班職位最高者。系統永遠不會自動送出通報，只能由組長以上人員確認送出。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from ..db.models import Incident, IncidentReport
from ..ids import jsonable
from ..schemas.events import Event, Topic
from ..sim.roster import ROLE_LABEL, ROLE_RANK
from .notifier import Message

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

DEADLINE_H = 8
SCENE_NOTICE = "非經司法機關或檢查機構許可，不得移動或破壞現場"
LEGAL_BASIS = "職業安全衛生法第37條第2項：發生死亡、罹災 3 人以上、罹災 1 人以上需住院治療等職業災害，應於 8 小時內通報勞動檢查機構"
COUNTDOWN_EVERY_S = 60


def criteria_met(c: dict | None) -> list[str]:
    if not c:
        return []
    out = []
    if (c.get("deaths") or 0) >= 1:
        out.append("死亡")
    if (c.get("injured") or 0) >= 3:
        out.append("罹災 3 人以上")
    if (c.get("hospitalized") or 0) >= 1:
        out.append("罹災需住院")
    if c.get("designated"):
        out.append("其他經中央主管機關指定公告之災害")
    return out


class ReportService:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self._last_tick: dict[str, Any] = {}

    def get(self, incident_id: str) -> IncidentReport | None:
        return self.rt.db.first(select(IncidentReport).where(IncidentReport.incident_id == incident_id)
                                .order_by(col(IncidentReport.id).desc()))

    async def set_casualty(self, incident_id: str, *, deaths: int = 0, injured: int = 0, hospitalized: int = 0,
                           designated: bool = False, actor: str, note: str = "") -> tuple[Incident, IncidentReport | None]:
        rt = self.rt
        now = rt.clock.now()
        with rt.db.session() as s:
            inc = s.get(Incident, incident_id)
            if inc is None:
                raise KeyError(incident_id)
            inc.casualty = {"deaths": deaths, "injured": injured, "hospitalized": hospitalized,
                            "designated": designated, "note": note, "set_by": actor, "set_at": now.isoformat()}
            s.add(inc)
            s.commit()
            s.refresh(inc)
        rt.audit.record("casualty_marked", actor=actor, trace_id=inc.trace_id, incident_id=incident_id,
                        casualty=inc.casualty)
        met = criteria_met(inc.casualty)
        report = self.get(incident_id)
        if met and report is None:
            report = await self._create(inc, met)
        await rt.bus.publish(self._event(inc, "casualty_marked", {"casualty": inc.casualty, "criteria_met": met,
                                                                   "report_required": bool(met)}))
        return inc, report

    async def _create(self, inc: Incident, met: list[str]) -> IncidentReport:
        rt = self.rt
        now = rt.clock.now()
        m = rt.plant.machines.get(inc.machine_id or "")
        line = rt.plant.line(inc.line)
        c = inc.casualty or {}
        draft = {
            "data_label": "模擬資料",
            "事業單位": "（模擬）Sentinel 示範工廠",
            "發生時間": inc.opened_at.isoformat(),
            "發生地點": f"{line.name if line else inc.line} {inc.machine_id or inc.camera_id or ''} {m.name if m else ''}".strip(),
            "災害經過": "；".join(list(rt.windows.timeline.get(inc.machine_id or inc.camera_id or "", []))[-8:])
                     or f"{inc.type}（{inc.rule_id}）",
            "傷亡概況": f"死亡 {c.get('deaths', 0)} 人、罹災 {c.get('injured', 0)} 人、需住院 {c.get('hospitalized', 0)} 人（待現場確認）",
            "符合通報條件": met,
            "通報對象": "勞動檢查機構",
            "法源": LEGAL_BASIS,
            "現場保全": SCENE_NOTICE,
            "備註": "本草稿由系統預填，須由主管確認內容後送出；系統不會自動送出。",
        }
        rep = IncidentReport(incident_id=inc.id, deadline=inc.opened_at + timedelta(hours=DEADLINE_H), draft=draft,
                             created_at=now)
        rt.db.add(rep)
        rt.audit.record("incident_report_created", trace_id=inc.trace_id, incident_id=inc.id,
                        deadline=rep.deadline.isoformat(), criteria_met=met)
        await self._notify(inc, met, rep)
        await rt.bus.publish(self._event(inc, "report_required", self.status(inc, rep)))
        return rep

    async def _notify(self, inc: Incident, met: list[str], rep: IncidentReport) -> None:
        rt = self.rt
        now = rt.clock.now()
        people = rt.roster.plant_role("safety_officer", now) + rt.roster.plant_role("first_aider", now)
        on_line = [p for p in rt.roster.on_shift(inc.line, now) if p.role in ROLE_RANK]
        top = max(on_line, key=lambda p: ROLE_RANK.get(p.role, 0), default=None)
        recipients = people + ([top] if top else [])
        wo = rt.workorders.for_incident(inc.id)
        lines = [f"符合職安法第37條第2項：{'、'.join(met)}", f"⚠ {SCENE_NOTICE}",
                 f"通報期限：{rep.deadline:%m/%d %H:%M}（8 小時內，由主管確認後送出）",
                 "通知：" + "、".join(f"{ROLE_LABEL.get(p.role, p.role)} {p.name}" for p in recipients)]
        msg = Message(channel="emergency", title=f"🚨 職業災害通報：{inc.id}", lines=lines, severity="S3",
                      workorder_id=wo.id if wo else inc.id, buttons=[])
        if wo:
            await rt.dispatch._send(wo, inc, msg, "casualty")
        else:
            await rt.notifier.send(msg)
        rt.audit.record("casualty_notified", trace_id=inc.trace_id, incident_id=inc.id,
                        recipients=[p.id for p in recipients])

    def status(self, inc: Incident, rep: IncidentReport | None) -> dict:
        now = self.rt.clock.now()
        met = criteria_met(inc.casualty)
        if rep is None:
            return {"incident_id": inc.id, "required": bool(met), "criteria_met": met, "casualty": inc.casualty,
                    "status": "none", "deadline": None, "remaining_s": None, "draft": None,
                    "instructions": [], "submitted_by": None, "submitted_at": None}
        remaining = (rep.deadline - now).total_seconds()
        st = "submitted" if rep.submitted_at else ("overdue" if remaining <= 0 else "draft")
        return {"incident_id": inc.id, "required": True, "criteria_met": met, "casualty": inc.casualty, "status": st,
                "deadline": rep.deadline.isoformat(), "remaining_s": max(0, int(remaining)), "draft": rep.draft,
                "instructions": [SCENE_NOTICE, "由主管確認草稿後按「送出通報」；系統不會自動送出"],
                "submitted_by": rep.submitted_by,
                "submitted_at": rep.submitted_at.isoformat() if rep.submitted_at else None}

    async def submit(self, incident_id: str, actor: str, draft: dict | None = None) -> dict:
        rt = self.rt
        inc = rt.db.get(Incident, incident_id)
        rep = self.get(incident_id)
        if inc is None or rep is None:
            raise KeyError(incident_id)
        if rep.submitted_at:
            raise ValueError("通報已送出")
        with rt.db.session() as s:
            r = s.get(IncidentReport, rep.id)
            if draft:
                r.draft = jsonable({**r.draft, **draft})
            r.submitted_by, r.submitted_at = actor, rt.clock.now()
            s.add(r)
            s.commit()
            s.refresh(r)
            rep = r
        rt.audit.record("incident_report_submitted", actor=actor, trace_id=inc.trace_id, incident_id=incident_id)
        out = self.status(inc, rep)
        await rt.bus.publish(self._event(inc, "report_submitted", out))
        return out

    async def tick(self) -> None:
        """每 60 秒（模擬時間）推送一次倒數；逾期推送一次 report_overdue。"""
        now = self.rt.clock.now()
        for rep in self.rt.db.all(select(IncidentReport).where(col(IncidentReport.submitted_at).is_(None))):
            last = self._last_tick.get(rep.incident_id)
            if last is not None and (now - last).total_seconds() < COUNTDOWN_EVERY_S:
                continue
            self._last_tick[rep.incident_id] = now
            inc = self.rt.db.get(Incident, rep.incident_id)
            if inc is None:
                continue
            st = self.status(inc, rep)
            await self.rt.bus.publish(self._event(inc, "report_overdue" if st["status"] == "overdue"
                                                  else "report_countdown", st))

    def _event(self, inc: Incident, type_: str, payload: dict) -> Event:
        return Event(ts=self.rt.clock.now(), topic=Topic.ALERTS, source="dispatch.report", line=inc.line,
                     machine_id=inc.machine_id, camera_id=inc.camera_id, type=type_, trace_id=inc.trace_id,
                     payload={"incident_id": inc.id, **payload})
