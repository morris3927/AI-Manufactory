// 前端模擬後端（NEXT_PUBLIC_MOCK=1）。
// 在瀏覽器記憶體裡維護工單狀態機、Log 模擬器與劇本，並以 §7.2 的 WebSocket 格式推送事件。
// 這裡所有資料都是模擬資料。

import { ApiError } from "../errors";
import { PERSON_ROLE_LABEL } from "../labels";
import type { Persona } from "../session";
import type {
  AgentMessage,
  AuditEntry,
  Box,
  Calibration,
  Category,
  ChecklistItem,
  DemoSettings,
  Diagnosis,
  Incident,
  LotoChecklist,
  MetricsSummary,
  Plant,
  SafetyFlag,
  SentinelEvent,
  Severity,
  SignalPoint,
  SopChunk,
  WorkOrderDetail,
  WorkOrderStatus,
  WritebackCheck,
  NextAction,
  IncidentReportStatus,
  KbCase,
  Health,
  WsMessage,
  SimilarCase,
} from "../types";
import {
  CHECKLIST_FEEDER,
  CHECKLIST_GENERIC,
  CHECKLIST_SAFETY,
  ESCALATION_CHAIN,
  FEED_JAM_EXPLANATION,
  LOTO_FEEDER,
  PEOPLE,
  PLANT,
  SCENARIOS,
  SOP_FEEDER,
  SOP_PPE,
  VIDEOS,
  feedJamDiagnosis,
} from "./data";

// ---------------------------------------------------------------- 工具
const iso = (ms: number = Date.now()) => new Date(ms).toISOString();
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const rnd = (a: number, b: number) => a + Math.random() * (b - a);
const noise = (amp: number) => (Math.random() - 0.5) * 2 * amp;
let idSeq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36).toUpperCase()}${(idSeq++ % 1296).toString(36).toUpperCase().padStart(2, "0")}`;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- 匯流
type Listener = (m: WsMessage) => void;
const listeners = new Set<Listener>();

function emit(channel: string, type: string, data: unknown) {
  const msg: WsMessage = { channel, type, data, ts: iso() };
  setTimeout(() => listeners.forEach((l) => l(msg)), 0);
}

export const mockBus = {
  subscribe(l: Listener) {
    listeners.add(l);
    start();
    return () => {
      listeners.delete(l);
    };
  },
};

// ---------------------------------------------------------------- 機台模擬
type Mode = "normal" | "ramp" | "jam" | "idle_mismatch" | "smoke" | "stopped";
interface Segment {
  at: number;
  mode: Mode;
}

const MACHINES = PLANT.lines.flatMap((l) => l.machines.map((m) => ({ ...m, line: l.id })));

function modeAt(segs: Segment[], t: number): { mode: Mode; since: number } {
  let cur: Segment = { at: 0, mode: "normal" };
  for (const s of segs) if (s.at <= t) cur = s;
  return { mode: cur.mode, since: cur.at };
}

function sample(machineId: string, t: number): SignalPoint {
  const { mode, since } = modeAt(S.segments[machineId] ?? [], t);
  const dt = (t - since) / 1000;
  const type = MACHINES.find((m) => m.id === machineId)?.type;
  const p: SignalPoint = { ts: iso(t) };
  if (type === "servo_roll_feeder") {
    let feed = 42 + noise(0.6);
    let err = 0.02 + noise(0.004);
    let cur = 8.2 + noise(0.3);
    let state = 1;
    if (mode === "ramp") {
      err = 0.02 + 0.19 * Math.min(1, dt / 6) + noise(0.004);
      feed = 42 - 6 * Math.min(1, dt / 6) + noise(0.6);
    } else if (mode === "jam") {
      feed = 0;
      err = 0.21 + noise(0.003);
      cur = 1.1 + noise(0.1);
      state = 2;
    }
    Object.assign(p, {
      feed_rate: +feed.toFixed(2),
      feed_length_error_mm: +Math.max(0, err).toFixed(3),
      motor_current_a: +cur.toFixed(2),
      motor_temp_c: +(52 + noise(0.4)).toFixed(1),
      state_code: state,
    });
  } else if (type === "servo_press") {
    const idle = mode === "idle_mismatch";
    const jam = modeAt(S.segments["STP-L1-FD02"] ?? [], t).mode === "jam";
    Object.assign(p, {
      spm: jam ? 0 : +(40 + noise(0.5)).toFixed(1),
      tonnage_peak: jam ? 0 : +(182 + noise(4)).toFixed(1),
      motor_temp_c: +(61 + noise(0.5)).toFixed(1),
      motion_score: idle || jam ? +(0.01 + Math.random() * 0.01).toFixed(3) : +(0.62 + noise(0.05)).toFixed(3),
      state_code: jam ? 2 : 1,
    });
  } else {
    const smoke = mode === "smoke";
    Object.assign(p, {
      motor_current_a: +(machineId.includes("CV") ? 5.4 + noise(0.25) : 12.5 + noise(0.5)).toFixed(2),
      motor_temp_c: +((smoke ? 78 : 48) + noise(0.6)).toFixed(1),
      motion_score: +(0.55 + noise(0.06)).toFixed(3),
      state_code: 1,
    });
  }
  return p;
}

// ---------------------------------------------------------------- 狀態
interface State {
  incidents: Map<string, Incident>;
  workorders: Map<string, WorkOrderDetail>;
  events: SentinelEvent[];
  audit: AuditEntry[];
  history: Record<string, SignalPoint[]>;
  segments: Record<string, Segment[]>;
  cameraSources: Record<string, string>;
  settings: DemoSettings;
  learned: boolean;
  cost: { ts: string; cost: number }[];
  costTotal: number;
  handled: { rules_only: number; jev: number; llm: number };
  calls: { jev: number; llm: number };
  woSeq: number;
  incSeq: number;
  agentTurns: Record<string, number>;
  timers: ReturnType<typeof setTimeout>[];
  reports: Map<string, IncidentReportStatus>;
  cases: Map<string, KbCase & { learned?: boolean }>;
}

const S: State = {
  incidents: new Map(),
  workorders: new Map(),
  events: [],
  audit: [],
  history: {},
  segments: {},
  cameraSources: {},
  settings: { escalation_timeout_s: 30, jev_enabled: true },
  learned: false,
  cost: [],
  costTotal: 0,
  handled: { rules_only: 0, jev: 0, llm: 0 },
  calls: { jev: 0, llm: 0 },
  woSeq: 1044,
  incSeq: 2044,
  agentTurns: {},
  timers: [],
  reports: new Map(),
  cases: new Map(),
};

const isSafetyCat = (c: Category) => c === "safety";

// ---------------------------------------------------------------- 職災 8 小時通報（職安法第37條第2項）
const SCENE_NOTICE = "非經司法機關或檢查機構許可，不得移動或破壞現場";

function criteriaMet(c: { deaths: number; injured: number; hospitalized: number; designated: boolean }) {
  const out: string[] = [];
  if (c.deaths >= 1) out.push("死亡");
  if (c.injured >= 3) out.push("罹災 3 人以上");
  if (c.hospitalized >= 1) out.push("罹災需住院");
  if (c.designated) out.push("其他經中央主管機關指定公告之災害");
  return out;
}

function reportStatus(incId: string): IncidentReportStatus {
  const inc = S.incidents.get(incId)!;
  const r = S.reports.get(incId);
  if (!r) return { incident_id: incId, required: false, criteria_met: [], casualty: inc.casualty ?? null, status: "none", deadline: null, remaining_s: null, draft: null, instructions: [], submitted_by: null, submitted_at: null };
  const remaining = r.deadline ? Math.floor((new Date(r.deadline).getTime() - Date.now()) / 1000) : null;
  return { ...r, status: r.submitted_at ? "submitted" : remaining !== null && remaining <= 0 ? "overdue" : "draft", remaining_s: remaining === null ? null : Math.max(0, remaining) };
}

function reportSummary(incId: string) {
  const r = reportStatus(incId);
  return { status: r.status, deadline: r.deadline, remaining_s: r.remaining_s, criteria_met: r.criteria_met, instructions: r.instructions };
}

function setCasualty(incId: string, c: { deaths: number; injured: number; hospitalized: number; designated: boolean; note?: string }, actor: string) {
  const inc = S.incidents.get(incId);
  need(inc, 404, "找不到異常");
  inc.casualty = { ...c, set_by: actor, set_at: iso() };
  audit(inc.trace_id, actor, "casualty_marked", { incident_id: incId, casualty: inc.casualty });
  const met = criteriaMet(c);
  if (met.length && !S.reports.has(incId)) {
    const deadline = iso(new Date(inc.opened_at).getTime() + 8 * 3600e3);
    S.reports.set(incId, {
      incident_id: incId, required: true, criteria_met: met, casualty: inc.casualty, status: "draft", deadline, remaining_s: null,
      draft: {
        data_label: "模擬資料",
        事業單位: "（模擬）Sentinel 示範工廠",
        發生時間: inc.opened_at,
        發生地點: `${inc.line} ${inc.machine_id ?? inc.camera_id ?? ""}`,
        災害經過: `${inc.title ?? inc.type}（${inc.rule_id}）`,
        傷亡概況: `死亡 ${c.deaths} 人、罹災 ${c.injured} 人、需住院 ${c.hospitalized} 人（待現場確認）`,
        符合通報條件: met,
        通報對象: "勞動檢查機構",
        法源: "職業安全衛生法第37條第2項：發生死亡、罹災 3 人以上、罹災 1 人以上需住院治療等職業災害，應於 8 小時內通報勞動檢查機構",
        現場保全: SCENE_NOTICE,
        備註: "本草稿由系統預填，須由主管確認內容後送出；系統不會自動送出。",
      },
      instructions: [SCENE_NOTICE, "由主管確認草稿後按「送出通報」；系統不會自動送出"],
      submitted_by: null, submitted_at: null,
    });
    audit(inc.trace_id, "system", "incident_report_created", { incident_id: incId, deadline, criteria_met: met });
    emit("incidents", "report_required", { incident_id: incId, payload: reportStatus(incId) });
  }
  inc.incident_report = S.reports.has(incId) ? reportSummary(incId) : null;
  emit("incidents", "casualty_marked", { incident_id: incId, payload: { incident_id: incId, criteria_met: met, report_required: met.length > 0 } });
  return reportStatus(incId);
}

function audit(trace_id: string | null, actor: string, action: string, detail: Record<string, unknown> = {}, ts = Date.now()) {
  const e: AuditEntry = { ts: iso(ts), trace_id, actor, action, detail };
  S.audit.push(e);
  return e;
}

function addCost(kind: "jev" | "llm", ts = Date.now()) {
  // LLM（Ollama Cloud）是訂閱制，不逐次計價
  const c = kind === "jev" ? rnd(0.0009, 0.0014) : 0;
  S.costTotal += c;
  S.calls[kind] += 1;
  S.cost.push({ ts: iso(ts), cost: c }); // 單次成本，computeSummary 再累加
}

function pushEvent(e: Omit<SentinelEvent, "event_id" | "ts"> & { ts?: string }) {
  const ev: SentinelEvent = { event_id: uid("EV"), ts: e.ts ?? iso(), ...e };
  if (ev.type !== "mes_log") {
    S.events.push(ev);
    if (S.events.length > 300) S.events.shift();
  }
  emit("events", ev.type, ev);
  return ev;
}

// ---------------------------------------------------------------- 建立 Incident／工單
interface IncidentSpec {
  line: string;
  machine_id: string;
  camera_id?: string;
  type: string;
  title: string;
  category: Category;
  rule_id: string;
  severity: Severity;
  facts: Record<string, unknown>;
  jev_box?: Box | null;
  diagnosis?: Diagnosis | null;
  status?: string;
  opened_at?: number;
  closed_at?: number | null;
  suspected_false_alarm?: boolean;
}

function makeIncident(spec: IncidentSpec): Incident {
  const id = `INC-${++S.incSeq}`;
  const inc: Incident = {
    id,
    trace_id: `TR-${Math.random().toString(36).slice(2, 10).toUpperCase()}`,
    line: spec.line,
    machine_id: spec.machine_id,
    camera_id: spec.camera_id ?? null,
    type: spec.type,
    title: spec.title,
    category: spec.category,
    rule_id: spec.rule_id,
    rule_severity: spec.severity,
    final_severity: spec.severity,
    facts: spec.facts,
    status: spec.status ?? "open",
    opened_at: iso(spec.opened_at),
    closed_at: spec.closed_at ? iso(spec.closed_at) : null,
    suspected_false_alarm: spec.suspected_false_alarm ?? false,
    keyframe_url: null,
    jev_box: spec.jev_box ?? null,
    diagnosis: spec.diagnosis ? { ...spec.diagnosis, incident_id: id } : null,
  };
  S.incidents.set(id, inc);
  return inc;
}

function genericLoto(machineType: string, machineName: string): LotoChecklist {
  return {
    loto_id: `LOTO-${machineType.toUpperCase().slice(0, 6)}`,
    machine_type: machineType,
    title: `${machineName} 上鎖掛牌程序`,
    steps: [
      { step_no: 1, text: "通知產線人員即將停機上鎖", energy: "通知", checked_by: null, ts: null },
      { step_no: 2, text: "正常停機並關閉主電源，上鎖掛牌", energy: "電力", checked_by: null, ts: null },
      { step_no: 3, text: "釋放氣壓／油壓等儲存能量", energy: "儲能", checked_by: null, ts: null },
      { step_no: 4, text: "零能量驗證", energy: "驗證", checked_by: null, ts: null },
    ],
  };
}

function pickAssignee(role: string, line?: string) {
  return PEOPLE.find((p) => p.role === role && (!line || !p.line || p.line === line)) ?? PEOPLE.find((p) => p.role === role)!;
}

interface WoSpec {
  role: string;
  created_at?: number;
  deadline_s?: number;
  sops?: SopChunk[];
  checklist?: ChecklistItem[];
  loto?: LotoChecklist | null;
}

function makeWorkorder(inc: Incident, spec: WoSpec, silent = false): WorkOrderDetail {
  const id = `WO-${++S.woSeq}`;
  const created = spec.created_at ?? Date.now();
  const person = pickAssignee(spec.role, inc.line);
  const machine = MACHINES.find((m) => m.id === inc.machine_id);
  const isSafety = inc.category === "safety";
  const wo: WorkOrderDetail = {
    id,
    incident_id: inc.id,
    status: "ASSIGNED",
    assignee_id: person.id,
    assignee_name: person.name,
    assignee_role: person.role,
    escalation_level: 0,
    ack_deadline: iso(created + (spec.deadline_s ?? S.settings.escalation_timeout_s) * 1000),
    created_at: iso(created),
    acked_at: null,
    closed_at: null,
    line: inc.line,
    machine_id: inc.machine_id,
    severity: inc.final_severity,
    category: inc.category,
    title: inc.title,
    trace_id: inc.trace_id,
    incident: inc,
    diagnosis: inc.diagnosis ?? null,
    sops: spec.sops ?? (isSafety ? SOP_PPE : []),
    loto: isSafety ? null : (spec.loto ?? (machine ? genericLoto(machine.type, machine.name) : null)),
    permit: null,
    checklist: clone(spec.checklist ?? (isSafety ? CHECKLIST_SAFETY : CHECKLIST_GENERIC)),
    agent_messages: [],
    transitions: [
      { from: null, to: "OPEN", actor: "system", reason: `規則 ${inc.rule_id} 命中`, ts: iso(created - 150) },
      { from: "OPEN", to: "ASSIGNED", actor: "system", reason: `依班表指派 ${person.name}（${PERSON_ROLE_LABEL[person.role] ?? person.role}）`, ts: iso(created) },
    ],
    report: null,
  };
  S.workorders.set(id, wo);
  audit(inc.trace_id, "system", "workorder_created", { workorder_id: id, assignee: person.name, role: person.role }, created);
  audit(inc.trace_id, "system", "notify_discord", { channel: `#${inc.line}`, workorder_id: id, embed: "嚴重度、根因前三名、SOP、接單按鈕" }, created + 200);
  if (!silent) emit("workorders", "created", summary(wo));
  return wo;
}

function nextActionOf(wo: WorkOrderDetail): NextAction | null {
  const st = wo.status;
  if (wo.category === "safety") return st === "ASSIGNED" || st === "ESCALATED" ? "ack" : st === "ACKED" ? "report" : null;
  const m: Partial<Record<WorkOrderStatus, NextAction>> = {
    OPEN: "assign", ASSIGNED: "ack", ESCALATED: "ack", ACKED: "permit_request", PERMIT_REQUESTED: "permit_approve",
    PERMIT_APPROVED: "loto", LOTO_DONE: "loto", IN_REPAIR: "checklist", PENDING_VERIFY: "report",
  };
  return m[st] ?? null;
}

/** 對外輸出：加上 workflow（與後端 serialize 相同） */
function out(wo: WorkOrderDetail): WorkOrderDetail {
  const c = clone(wo);
  c.workflow = { require_permit: true, next_action: nextActionOf(wo) };
  c.incident = { ...c.incident, incident_report: S.reports.has(wo.incident_id) ? reportSummary(wo.incident_id) : null };
  return c;
}

function summary(wo: WorkOrderDetail) {
  return out(wo);
}

function transition(wo: WorkOrderDetail, to: WorkOrderStatus, actor: string, reason?: string) {
  const from = wo.status;
  wo.status = to;
  wo.transitions = [...(wo.transitions ?? []), { from, to, actor, reason: reason ?? null, ts: iso() }];
  audit(wo.trace_id ?? null, actor, "workorder_transition", { workorder_id: wo.id, from, to, reason });
  emit("workorders", "status_changed", { id: wo.id, from, to, workorder: summary(wo) });
}

// ---------------------------------------------------------------- 種子資料
function seedHistoric(now: number) {
  const hist: { spec: IncidentSpec; role: string; ackMin: number; closeMin: number | null; cancelled?: boolean }[] = [
    {
      spec: { line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "die_temp_high", title: "模具溫度偏高", category: "equipment", rule_id: "R-STP-DIETEMP", severity: "S1", facts: { die_temp_c: 88, threshold: 85 }, opened_at: now - 6 * 3600e3 },
      role: "technician", ackMin: 3.2, closeMin: 34,
    },
    {
      spec: { line: "assembly", machine_id: "ASM-L1-WS01", camera_id: "CAM-ASM-01", type: "ppe_violation", title: "人員未戴安全帽", category: "safety", rule_id: "R-SAF-PPE", severity: "S2", facts: { missing: ["helmet"], conf: 0.9 }, opened_at: now - 5.2 * 3600e3 },
      role: "leader", ackMin: 1.1, closeMin: 6,
    },
    {
      spec: { line: "welding", machine_id: "WLD-L1-RB01", camera_id: "CAM-WLD-01", type: "weld_smoke", title: "銲接煙霧異常", category: "equipment", rule_id: "R-WLD-SMOKE", severity: "S2", facts: { p_smoke_last2: [0.81, 0.86] }, opened_at: now - 4.1 * 3600e3 },
      role: "technician", ackMin: 2.4, closeMin: 27,
    },
    {
      spec: { line: "stamping", machine_id: "STP-L1-FD02", camera_id: "CAM-STP-01", type: "feed_error_high", title: "送料長度誤差偏大", category: "equipment", rule_id: "R-STP-FEEDERR", severity: "S1", facts: { feed_length_error_mm: 0.09 }, opened_at: now - 3.2 * 3600e3 },
      role: "technician", ackMin: 4.0, closeMin: 19,
    },
    {
      spec: { line: "assembly", machine_id: "ASM-L1-CV01", type: "conveyor_stop", title: "輸送帶停止", category: "process", rule_id: "R-ASM-CVSTOP", severity: "S2", facts: { motion_score: 0.01 }, opened_at: now - 2.1 * 3600e3, suspected_false_alarm: true },
      role: "technician", ackMin: 1.8, closeMin: 4, cancelled: true,
    },
    {
      spec: { line: "welding", machine_id: "WLD-L1-FX01", type: "clamp_fault", title: "治具夾持異常", category: "equipment", rule_id: "R-WLD-CLAMP", severity: "S1", facts: { clamp_pressure_bar: 3.1 }, opened_at: now - 1.5 * 3600e3 },
      role: "technician", ackMin: 2.9, closeMin: 22,
    },
  ];
  S.woSeq = 1030;
  S.incSeq = 2030;
  for (const h of hist) {
    const opened = h.spec.opened_at!;
    const inc = makeIncident({ ...h.spec, status: h.cancelled ? "cancelled" : "closed", closed_at: h.closeMin ? opened + h.closeMin * 60e3 : null });
    audit(inc.trace_id, "rules", "rule_hit", { rule_id: inc.rule_id, severity: inc.rule_severity, facts: inc.facts }, opened);
    if (inc.category === "equipment") {
      audit(inc.trace_id, "jev", "jev_event_judgement", { purpose: "J1-J3", latency_ms: 540 }, opened + 500);
      S.handled.jev++;
      addCost("jev", opened + 500);
      addCost("llm", opened + 2000);
      S.handled.llm++;
    } else S.handled.rules_only++;
    const wo = makeWorkorder(inc, { role: h.role, created_at: opened + 700, deadline_s: 300 }, true);
    S.woSeq++;
    const ackAt = opened + 700 + h.ackMin * 60e3;
    const closeAt = opened + (h.closeMin ?? 10) * 60e3;
    wo.acked_at = iso(ackAt);
    wo.ack_deadline = null;
    wo.closed_at = iso(closeAt);
    wo.status = h.cancelled ? "CANCELLED" : "CLOSED";
    wo.loto?.steps.forEach((s) => {
      s.checked_by = wo.assignee_id;
      s.ts = iso(ackAt + s.step_no * 30e3);
    });
    if (!isSafetyCat(inc.category)) wo.permit = { status: "approved", requested_by: wo.assignee_id, requested_at: iso(ackAt + 60e3), approved_by: "P-L01", ts: iso(ackAt + 200e3) };
    wo.checklist?.forEach((c) => {
      c.checked_by = wo.assignee_id;
      c.ts = iso(closeAt - 60e3);
    });
    wo.transitions!.push(
      { from: "ASSIGNED", to: "ACKED", actor: wo.assignee_id!, reason: "Discord 接單", ts: iso(ackAt) },
      h.cancelled
        ? { from: "ACKED", to: "CANCELLED", actor: "P-S01", reason: "主管標記誤報：輸送帶為計畫性停機", ts: iso(closeAt) }
        : { from: "PENDING_VERIFY", to: "CLOSED", actor: wo.assignee_id!, reason: "回報通過 J5 完整性檢查", ts: iso(closeAt) },
    );
    audit(inc.trace_id, wo.assignee_id!, "ack", { workorder_id: wo.id }, ackAt);
    audit(inc.trace_id, h.cancelled ? "P-S01" : wo.assignee_id!, h.cancelled ? "override" : "workorder_closed", { workorder_id: wo.id }, closeAt);
  }
}

function seedActive(now: number, includeFeedJam: boolean) {
  // WO-1040 銲接：待確認
  S.woSeq = 1039;
  S.incSeq = 2039;
  {
    const inc = makeIncident({ line: "welding", machine_id: "WLD-L1-RB01", camera_id: "CAM-WLD-01", type: "weld_spatter", title: "銲渣飛濺過量", category: "equipment", rule_id: "R-WLD-SPATTER", severity: "S2", facts: { spatter_index: 0.74 }, opened_at: now - 48 * 60e3, jev_box: [520, 260, 760, 470],
      diagnosis: { incident_id: "", root_cause_top3: [ { failure_mode_id: "FM-WLD-NOZZLE", name: "噴嘴積渣", p: 0.58 }, { failure_mode_id: "FM-WLD-GAS", name: "保護氣體流量不足", p: 0.27 }, { failure_mode_id: "FM-WLD-WIRE", name: "銲線送線不穩", p: 0.1 } ], confidence: 0.72, mode: "jev_top3", explanation: "飛濺指數持續高於 0.7，且電流波動加大，最常見的原因是噴嘴積渣。", explanation_status: "done" } });
    audit(inc.trace_id, "rules", "rule_hit", { rule_id: inc.rule_id }, now - 48 * 60e3);
    const wo = makeWorkorder(inc, { role: "technician", created_at: now - 48 * 60e3 + 800, deadline_s: 300 }, true);
    const ack = now - 44 * 60e3;
    wo.acked_at = iso(ack);
    wo.ack_deadline = null;
    wo.loto?.steps.forEach((s) => ((s.checked_by = wo.assignee_id), (s.ts = iso(ack + s.step_no * 40e3))));
    wo.permit = { status: "approved", requested_by: wo.assignee_id, requested_at: iso(ack + 60e3), approved_by: "P-L01", ts: iso(ack + 120e3) };
    wo.checklist?.forEach((c) => ((c.checked_by = wo.assignee_id), (c.ts = iso(now - 6 * 60e3))));
    wo.transitions!.push(
      { from: "ASSIGNED", to: "ACKED", actor: wo.assignee_id!, reason: "Discord 接單", ts: iso(ack) },
      { from: "ACKED", to: "PERMIT_REQUESTED", actor: wo.assignee_id!, reason: "申請作業許可", ts: iso(ack + 60e3) },
      { from: "PERMIT_REQUESTED", to: "PERMIT_APPROVED", actor: "P-L01", reason: "作業許可核准", ts: iso(ack + 120e3) },
      { from: "PERMIT_APPROVED", to: "LOTO_DONE", actor: wo.assignee_id!, ts: iso(ack + 240e3) },
      { from: "LOTO_DONE", to: "IN_REPAIR", actor: wo.assignee_id!, reason: "LOTO 完成並驗證零能量，開始維修", ts: iso(ack + 240e3) },
      { from: "IN_REPAIR", to: "PENDING_VERIFY", actor: wo.assignee_id!, reason: "Checklist 完成", ts: iso(now - 6 * 60e3) },
    );
    wo.status = "PENDING_VERIFY";
    S.handled.jev++;
    S.handled.llm++;
    addCost("jev", now - 48 * 60e3);
    addCost("llm", now - 47 * 60e3);
  }
  // WO-1041 組裝線 PPE：班長已接單
  S.woSeq = 1040;
  S.incSeq = 2040;
  {
    const t0 = now - 9 * 60e3;
    const inc = makeIncident({ line: "assembly", machine_id: "ASM-L1-WS01", camera_id: "CAM-ASM-01", type: "ppe_violation", title: "人員未戴安全帽", category: "safety", rule_id: "R-SAF-PPE", severity: "S2", facts: { missing: ["helmet"], conf: 0.91, jev_confirm: 0.93 }, opened_at: t0, jev_box: [610, 140, 820, 600] });
    audit(inc.trace_id, "vision.ppe", "ppe_violation", { conf: 0.91 }, t0 - 900);
    audit(inc.trace_id, "jev", "jev_vision_ppe", { p_violation: 0.93, latency_ms: 710 }, t0 - 200);
    audit(inc.trace_id, "rules", "rule_hit", { rule_id: inc.rule_id, route: "dispatch_only（工安 S2 不叫 LLM）" }, t0);
    const wo = makeWorkorder(inc, { role: "leader", created_at: t0 + 600, deadline_s: 300 }, true);
    wo.acked_at = iso(t0 + 70e3);
    wo.ack_deadline = null;
    wo.status = "ACKED";
    wo.transitions!.push({ from: "ASSIGNED", to: "ACKED", actor: "P-L01", reason: "Discord 接單", ts: iso(t0 + 70e3) });
    S.handled.jev++;
    addCost("jev", t0 - 200);
  }
  S.woSeq = 1041;
  S.incSeq = 2041;
  if (includeFeedJam) {
    const t0 = now - 150e3;
    S.segments["STP-L1-FD02"] = [
      { at: 0, mode: "normal" },
      { at: t0 - 6e3, mode: "ramp" },
      { at: t0, mode: "jam" },
    ];
    createFeedJam(t0, true);
    S.cameraSources["CAM-STP-01"] = "assets/videos/stamping/feed_jam_01.mp4";
  }
  // 輸送帶：升級中（會在 Demo 中自動升到組長）
  {
    const t0 = now - 70e3;
    const inc = makeIncident({ line: "assembly", machine_id: "ASM-L1-CV01", type: "motor_current_high", title: "輸送帶馬達電流偏高", category: "equipment", rule_id: "R-ASM-CVCUR", severity: "S1", facts: { motor_current_a: 7.9, baseline: 5.4 }, opened_at: t0,
      diagnosis: { incident_id: "", root_cause_top3: [ { failure_mode_id: "FM-CV-BELT", name: "皮帶張力過大", p: 0.46 }, { failure_mode_id: "FM-CV-BEARING", name: "滾筒軸承磨損", p: 0.33 }, { failure_mode_id: "FM-CV-LOAD", name: "負載過重", p: 0.15 } ], confidence: 0.61, mode: "jev_top3", explanation: "電流比基準高 46%，溫度同步上升，先檢查皮帶張力。", explanation_status: "done" } });
    audit(inc.trace_id, "rules", "rule_hit", { rule_id: inc.rule_id }, t0);
    const wo = makeWorkorder(inc, { role: "leader", created_at: t0 + 500, deadline_s: 120 }, true);
    wo.escalation_level = 1;
    wo.transitions = [
      { from: null, to: "OPEN", actor: "system", reason: "規則 R-ASM-CVCUR 命中", ts: iso(t0) },
      { from: "OPEN", to: "ASSIGNED", actor: "system", reason: "依班表指派 張雅婷（技術員）", ts: iso(t0 + 500) },
      { from: "ASSIGNED", to: "ESCALATED", actor: "system", reason: "逾時未接單", ts: iso(t0 + 30e3) },
      { from: "ESCALATED", to: "ASSIGNED", actor: "system", reason: "升級指派 陳美玲（班長）", ts: iso(t0 + 30e3) },
    ];
    wo.ack_deadline = iso(now + 45e3);
    S.handled.jev++;
    addCost("jev", t0 + 300);
  }
}

function latestApprovedFeedJamCase() {
  return [...S.cases.values()].filter((c) => c.status === "approved" && c.event_type === "feed_jam").sort((a, b) => (b.reviewed_at ?? "").localeCompare(a.reviewed_at ?? ""))[0];
}

function rootCauseName(id: string | null | undefined) {
  return feedJamDiagnosis("", false).root_cause_top3.find((r) => r.failure_mode_id === id)?.name ?? id ?? "未確認";
}

function refSentence(c: KbCase) {
  return `引用剛核准的 ${c.case_id}：上次同一台送料機也是卡料，最後確認是${rootCauseName(c.final_root_cause)}。`;
}

function createFeedJam(t0: number, silent: boolean) {
  // 已核准入庫的卡料案例：會出現在下一次判斷的相似案例裡，但 top-1 機率不一定上升（實測 0.54 → 0.47）
  const ref = latestApprovedFeedJamCase();
  const jevOn = S.settings.jev_enabled;
  const base = feedJamDiagnosis("", false);
  if (ref) base.root_cause_top3 = base.root_cause_top3.map((r, i) => ({ ...r, p: [0.49, 0.33, 0.13][i] ?? r.p }));
  base.similar_cases = [
    ...(ref ? [{ case_id: ref.case_id, final_root_cause: ref.final_root_cause, final_root_cause_name: rootCauseName(ref.final_root_cause), status: "approved", just_approved: true, score: 0.91 }] : []),
    { case_id: "CASE-STP-0087", title: "送料卡料：板厚超差", final_root_cause_name: "材料厚度偏差", status: "approved", score: 0.82 },
    { case_id: "CASE-STP-0031", title: "送料長度偏短：滾輪打滑", final_root_cause_name: "送料滾輪磨損", status: "approved", score: 0.77 },
  ];
  const explanation = ref ? `${FEED_JAM_EXPLANATION}${refSentence(ref)}` : FEED_JAM_EXPLANATION;
  const diag: Diagnosis = jevOn
    ? { ...base, explanation: silent ? explanation : null, explanation_status: silent ? "done" : "pending" }
    : { incident_id: "", root_cause_top3: [], confidence: 0, mode: "rules_only", explanation: "AI 判斷暫不可用：Jev 已關閉，系統以規則（Log 送料速率歸零＋動態分數歸零）照常派工。", explanation_status: "failed" };
  const inc = makeIncident({
    line: "stamping",
    machine_id: "STP-L1-FD02",
    camera_id: "CAM-STP-01",
    type: "feed_jam",
    title: "沖床送料卡料",
    category: "equipment",
    rule_id: jevOn ? "R-STP-FEEDJAM" : "R-STP-FEEDJAM-FALLBACK",
    severity: "S2",
    opened_at: t0 + 100,
    facts: jevOn
      ? { p_jam_last2: [0.82, 0.88], feed_rate_before: 42, feed_rate_now: 0, seconds_since_drop: 0.1, feed_length_error_mm: 0.21, alarm_code: "MISFEED" }
      : { feed_rate_before: 42, feed_rate_now: 0, motion_score: 0.01, alarm_code: "MISFEED", jev: "unavailable" },
    jev_box: jevOn ? [540, 380, 790, 560] : null,
    diagnosis: diag,
  });
  const tr = inc.trace_id;
  audit(tr, "sim.mes", "mes_anomaly", { signal: "feed_length_error_mm", from: 0.02, to: 0.21 }, t0 - 6000);
  if (jevOn) {
    audit(tr, "jev", "jev_vision_feed", { purpose: "V2", p_jam: 0.82, box: [540, 380, 790, 560], latency_ms: 820 }, t0 - 4000);
    audit(tr, "jev", "jev_vision_feed", { purpose: "V2", p_jam: 0.88, latency_ms: 760, trigger: "log 異常加送關鍵幀" }, t0);
  }
  audit(tr, "rules", "rule_hit", { rule_id: inc.rule_id, severity: "S2", category: "equipment", facts: inc.facts }, t0 + 100);
  if (jevOn) {
    audit(tr, "kb", "similar_cases", { k: 5, top: ["CASE-STP-0087", "CASE-STP-0031", "CASE-STP-0102"] }, t0 + 200);
    audit(tr, "jev", "jev_event_judgement", { purpose: "J1-J3", questions_ver: "jq-2026.10.3", top3: diag.root_cause_top3.map((r) => `${r.name} ${r.p}`), latency_ms: 530 }, t0 + 700);
    addCost("jev", t0 - 4000);
    addCost("jev", t0);
    addCost("jev", t0 + 700);
    S.handled.jev++;
  } else {
    audit(tr, "jev", "jev_unavailable", { fallback: "rules_only" }, t0 + 150);
    S.handled.rules_only++;
  }
  const wo = makeWorkorder(inc, { role: "technician", created_at: t0 + 800, deadline_s: silent ? 600 : S.settings.escalation_timeout_s, loto: clone(LOTO_FEEDER), checklist: CHECKLIST_FEEDER, sops: SOP_FEEDER }, silent);
  if (silent && jevOn) {
    audit(tr, "llm", "llm_explanation", { model: "deepseek-v4.1-flash", latency_ms: 1620, tokens: 412 }, t0 + 2400);
    addCost("llm", t0 + 2400);
    S.handled.llm++;
  }
  return { inc, wo };
}

let started = false;
let tickTimer: ReturnType<typeof setInterval> | null = null;
let metricsTimer: ReturnType<typeof setInterval> | null = null;

function seed(includeFeedJam: boolean) {
  S.incidents.clear();
  S.workorders.clear();
  S.events = [];
  S.audit = [];
  S.cost = [];
  S.costTotal = 0.0185;
  S.handled = { rules_only: 11, jev: 6, llm: 4 };
  S.calls = { jev: 14, llm: 4 };
  S.agentTurns = {};
  S.segments = {};
  S.reports = new Map();
  S.cases = new Map();
  S.cameraSources = Object.fromEntries(PLANT.lines.flatMap((l) => l.cameras.map((c) => [c.id, c.source ?? ""])));
  const now = Date.now();
  // AI 成本歷史（早上的呼叫）
  for (let i = 0; i < 12; i++) S.cost.push({ ts: iso(now - (8 - i * 0.6) * 3600e3), cost: 0.0185 / 12 });
  seedHistoric(now);
  seedActive(now, includeFeedJam);
  S.cost.sort((a, b) => a.ts.localeCompare(b.ts));
  // 訊號歷史（10 分鐘）
  S.history = {};
  for (const m of MACHINES) {
    const arr: SignalPoint[] = [];
    for (let t = now - 600e3; t <= now; t += 1000) arr.push(sample(m.id, t));
    S.history[m.id] = arr;
  }
  S.audit.sort((a, b) => a.ts.localeCompare(b.ts));
}

function start() {
  if (started || typeof window === "undefined") return;
  started = true;
  seed(true);
  tickTimer = setInterval(tick, 1000);
  metricsTimer = setInterval(() => emit("metrics", "summary", computeSummary()), 5000);
}

let lastCountdown = 0;
function tick() {
  const now = Date.now();
  if (now - lastCountdown > 60000) {
    lastCountdown = now;
    for (const [id, r] of S.reports) if (!r.submitted_at) emit("incidents", reportStatus(id).status === "overdue" ? "report_overdue" : "report_countdown", { incident_id: id, payload: { incident_id: id, ...reportSummary(id) } });
  }
  for (const m of MACHINES) {
    const p = sample(m.id, now);
    const arr = (S.history[m.id] ??= []);
    arr.push(p);
    if (arr.length > 900) arr.shift();
    pushEvent({ topic: "mes.logs", source: "sim.mes", line: m.line, machine_id: m.id, camera_id: null, type: "mes_log", payload: p, trace_id: null, ts: p.ts });
  }
  // 逐級升級（RU-5）
  for (const wo of S.workorders.values()) {
    if (wo.status !== "ASSIGNED" || !wo.ack_deadline) continue;
    if (new Date(wo.ack_deadline).getTime() > now) continue;
    const idx = ESCALATION_CHAIN.indexOf(wo.assignee_role ?? "technician");
    if (idx < 0 || idx >= ESCALATION_CHAIN.length - 1) {
      wo.ack_deadline = null;
      emit("workorders", "updated", summary(wo));
      continue;
    }
    const next = pickAssignee(ESCALATION_CHAIN[idx + 1]);
    transition(wo, "ESCALATED", "system", `逾時 ${S.settings.escalation_timeout_s} 秒未接單`);
    wo.escalation_level += 1;
    wo.assignee_id = next.id;
    wo.assignee_name = next.name;
    wo.assignee_role = next.role;
    wo.ack_deadline = iso(now + S.settings.escalation_timeout_s * 1000);
    transition(wo, "ASSIGNED", "system", `升級指派 ${next.name}`);
    audit(wo.trace_id ?? null, "system", "notify_discord", { channel: "#升級", workorder_id: wo.id, to: next.name });
    emit("workorders", "escalated", { id: wo.id, escalation_level: wo.escalation_level, assignee: next.name, workorder: summary(wo) });
  }
}

function later(ms: number, fn: () => void) {
  S.timers.push(setTimeout(fn, ms));
}

// ---------------------------------------------------------------- 劇本
function runScenario(id: string): { ok: boolean; trace_id?: string } {
  const now = Date.now();
  if (id === "feed_jam") {
    const open = [...S.incidents.values()].find((i) => i.type === "feed_jam" && i.status === "open");
    if (open) {
      // RU-6 去重：同機台同類型未結案 → 更新原 Incident
      audit(open.trace_id, "rules", "incident_dedup", { note: "同機台同類型未結案，更新原異常" });
      emit("incidents", "updated", open);
      return { ok: true, trace_id: open.trace_id, deduped: true } as { ok: boolean; trace_id?: string };
    }
    setSource("CAM-STP-01", "assets/videos/stamping/feed_jam_01.mp4");
    pushEvent({ topic: "vision.events", source: "control", line: "stamping", machine_id: null, camera_id: "CAM-STP-01", type: "scenario_started", payload: { scenario: "feed_jam", title: "場景 2：沖床送料卡料" }, trace_id: null });
    S.segments["STP-L1-FD02"] = [
      { at: 0, mode: "normal" },
      { at: now + 8000, mode: "ramp" },
      { at: now + 14000, mode: "jam" },
    ];
    if (S.settings.jev_enabled) {
      later(10000, () => {
        pushEvent({ topic: "vision.events", source: "vision.jev_feed", line: "stamping", machine_id: "STP-L1-FD02", camera_id: "CAM-STP-01", type: "jev_vision", payload: { p_jam: 0.82, p_smoke: 0.02, box: [540, 380, 790, 560], latency_ms: 820 }, trace_id: null });
        emit("ai", "jev_vision", { camera_id: "CAM-STP-01", p_jam: 0.82, box: [540, 380, 790, 560] });
      });
    }
    later(14000, () => {
      pushEvent({ topic: "mes.logs", source: "sim.mes", line: "stamping", machine_id: "STP-L1-FD02", camera_id: null, type: "machine_alarm", payload: { alarm_code: "MISFEED", feed_rate: 0 }, trace_id: null });
      if (S.settings.jev_enabled) {
        pushEvent({ topic: "vision.events", source: "vision.jev_feed", line: "stamping", machine_id: "STP-L1-FD02", camera_id: "CAM-STP-01", type: "jev_vision", payload: { p_jam: 0.88, p_smoke: 0.01, box: [540, 380, 790, 560], latency_ms: 760, trigger: "log_anomaly" }, trace_id: null });
      }
    });
    later(14100, () => {
      const { inc, wo } = createFeedJam(now + 14000, false);
      pushEvent({ topic: "alerts", source: "rules", line: "stamping", machine_id: "STP-L1-FD02", camera_id: "CAM-STP-01", type: "incident_opened", payload: { incident_id: inc.id, severity: "S2", rule_id: inc.rule_id, title: inc.title }, trace_id: inc.trace_id });
      emit("incidents", "created", inc);
      if (inc.diagnosis && inc.diagnosis.mode === "jev_top3") emit("ai", "jev_result", { incident_id: inc.id, workorder_id: wo.id, diagnosis: inc.diagnosis });
      if (S.settings.jev_enabled) {
        // LLM 說明約 1.5 秒後補上（串流）
        const ref = latestApprovedFeedJamCase();
        const full = ref ? `${FEED_JAM_EXPLANATION}${refSentence(ref)}` : FEED_JAM_EXPLANATION;
        const d = inc.diagnosis!;
        later(900, () => {
          d.explanation_status = "streaming";
          d.explanation = "";
        });
        const chunks = Math.ceil(full.length / 12);
        for (let i = 0; i < chunks; i++) {
          later(900 + i * 60, () => {
            d.explanation = full.slice(0, (i + 1) * 12);
            emit("ai", "llm_delta", { incident_id: inc.id, workorder_id: wo.id, text: d.explanation });
          });
        }
        later(900 + chunks * 60 + 50, () => {
          d.explanation = full;
          d.explanation_status = "done";
          wo.diagnosis = d;
          audit(inc.trace_id, "llm", "llm_explanation", { model: "deepseek-v4.1-flash", latency_ms: 900 + chunks * 60, tokens: 398 });
          audit(inc.trace_id, "system", "notify_discord_reply", { workorder_id: wo.id, note: "LLM 說明以回覆補上" });
          addCost("llm");
          S.handled.llm++;
          emit("ai", "llm_explanation", { incident_id: inc.id, workorder_id: wo.id, explanation: full, status: "done" });
        });
      }
    });
    later(40000, () => setSource("CAM-STP-01", "assets/videos/stamping/normal_01.mp4"));
    return { ok: true };
  }
  if (id === "ppe_helmet") {
    setSource("CAM-ASM-01", "assets/videos/assembly/no_helmet_01.mp4");
    pushEvent({ topic: "vision.events", source: "control", line: "assembly", machine_id: null, camera_id: "CAM-ASM-01", type: "scenario_started", payload: { scenario: "ppe_helmet", title: "場景 1：PPE 未戴安全帽" }, trace_id: null });
    later(2500, () => {
      pushEvent({ topic: "vision.events", source: "vision.ppe", line: "assembly", machine_id: "ASM-L1-WS01", camera_id: "CAM-ASM-01", type: "ppe_violation", payload: { person_box: [610, 140, 820, 600], missing: ["helmet"], conf: 0.91 }, trace_id: null });
    });
    later(3300, () => {
      if (S.settings.jev_enabled) {
        pushEvent({ topic: "vision.events", source: "vision.jev_ppe", line: "assembly", machine_id: "ASM-L1-WS01", camera_id: "CAM-ASM-01", type: "jev_vision", payload: { p_violation: 0.93, box: [640, 140, 800, 300], latency_ms: 710 }, trace_id: null });
        addCost("jev");
        S.handled.jev++;
      } else S.handled.rules_only++;
      const inc = makeIncident({ line: "assembly", machine_id: "ASM-L1-WS01", camera_id: "CAM-ASM-01", type: "ppe_violation", title: "人員未戴安全帽", category: "safety", rule_id: "R-SAF-PPE", severity: "S2", facts: { missing: ["helmet"], conf: 0.91, jev_confirm: S.settings.jev_enabled ? 0.93 : "unavailable" }, jev_box: [640, 140, 800, 300] });
      audit(inc.trace_id, "vision.ppe", "ppe_violation", { conf: 0.91, face_blur: true });
      if (S.settings.jev_enabled) audit(inc.trace_id, "jev", "jev_vision_ppe", { p_violation: 0.93, latency_ms: 710, note: "人員裁切圖已模糊人臉" });
      audit(inc.trace_id, "rules", "rule_hit", { rule_id: "R-SAF-PPE", route: "dispatch_only（工安 S2 不叫 LLM）" });
      pushEvent({ topic: "alerts", source: "rules", line: "assembly", machine_id: "ASM-L1-WS01", camera_id: "CAM-ASM-01", type: "incident_opened", payload: { incident_id: inc.id, severity: "S2", title: inc.title }, trace_id: inc.trace_id });
      emit("incidents", "created", inc);
      makeWorkorder(inc, { role: "leader" });
    });
    later(30000, () => setSource("CAM-ASM-01", "assets/videos/assembly/normal_01.mp4"));
    return { ok: true };
  }
  if (id === "idle_mismatch") {
    setSource("CAM-STP-02", "assets/videos/stamping/press_idle_01.mp4");
    pushEvent({ topic: "vision.events", source: "control", line: "stamping", machine_id: null, camera_id: "CAM-STP-02", type: "scenario_started", payload: { scenario: "idle_mismatch", title: "場景 3：影像閒置但 Log 顯示運轉" }, trace_id: null });
    S.segments["STP-L1-PR01"] = [{ at: 0, mode: "normal" }, { at: now + 1000, mode: "idle_mismatch" }];
    later(3000, () => pushEvent({ topic: "vision.events", source: "vision.motion", line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "machine_state", payload: { state: "idle", motion_score: 0.012 }, trace_id: null }));
    later(8000, () => {
      const inc = makeIncident({ line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "log_vision_mismatch", title: "影像閒置但 Log 顯示運轉", category: "process", rule_id: "R-X-IDLEMISMATCH", severity: "S1", facts: { motion_score_avg_5s: 0.012, mes_state: "running", mes_spm: 40, duration_s: 5 } });
      audit(inc.trace_id, "rules", "rule_hit", { rule_id: "R-X-IDLEMISMATCH", note: "跨模態：影像動態分數≈0，MES 仍回報 running／40 spm" });
      S.handled.rules_only++;
      pushEvent({ topic: "alerts", source: "rules", line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "incident_opened", payload: { incident_id: inc.id, severity: "S1", title: inc.title }, trace_id: inc.trace_id });
      emit("incidents", "created", inc);
      makeWorkorder(inc, { role: "technician", checklist: CHECKLIST_GENERIC });
    });
    later(45000, () => {
      setSource("CAM-STP-02", "assets/videos/stamping/press_normal_01.mp4");
      S.segments["STP-L1-PR01"] = [{ at: 0, mode: "normal" }];
    });
    return { ok: true };
  }
  if (id === "person_zone") {
    pushEvent({ topic: "vision.events", source: "control", line: "stamping", machine_id: null, camera_id: "CAM-STP-02", type: "scenario_started", payload: { scenario: "person_zone", title: "場景 6：光幕停用時人員進入沖床危險區" }, trace_id: null });
    later(2000, () => pushEvent({ topic: "mes.logs", source: "sim.mes", line: "stamping", machine_id: "STP-L1-PR01", camera_id: null, type: "machine_alarm", payload: { alarm_code: "LIGHT_CURTAIN_MUTED", feed_rate: "—" }, trace_id: null }));
    later(4000, () => {
      pushEvent({ topic: "vision.events", source: "vision.ppe", line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "ppe_violation", payload: { person_box: [520, 200, 700, 620], missing: [], conf: 0.88, zone: "danger_zone" }, trace_id: null });
      const inc = makeIncident({ line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "person_in_danger_zone", title: "光幕停用時人員進入沖床危險區", category: "safety", rule_id: "R-SAF-ZONE", severity: "S3", facts: { light_curtain_status: "MUTED", person_in_zone_s: 5, press_state: "running" }, jev_box: [520, 200, 700, 620] });
      audit(inc.trace_id, "rules", "rule_hit", { rule_id: "R-SAF-ZONE", severity: "S3", route: "emergency" });
      S.handled.rules_only++;
      pushEvent({ topic: "alerts", source: "rules", line: "stamping", machine_id: "STP-L1-PR01", camera_id: "CAM-STP-02", type: "incident_opened", payload: { incident_id: inc.id, severity: "S3", title: inc.title }, trace_id: inc.trace_id });
      emit("incidents", "created", inc);
      makeWorkorder(inc, { role: "supervisor" });
      // 劇本腳本設定傷亡（系統不會從影像推論傷亡）
      later(4000, () => setCasualty(inc.id, { deaths: 0, injured: 1, hospitalized: 1, designated: false, note: "劇本：右手壓傷送醫" }, "scenario"));
    });
    return { ok: true };
  }
  if (id === "weld_smoke") {
    setSource("CAM-WLD-01", "assets/videos/welding/smoke_01.mp4");
    pushEvent({ topic: "vision.events", source: "control", line: "welding", machine_id: null, camera_id: "CAM-WLD-01", type: "scenario_started", payload: { scenario: "weld_smoke", title: "場景 4：銲接煙霧＋逐級升級" }, trace_id: null });
    S.segments["WLD-L1-RB01"] = [{ at: 0, mode: "normal" }, { at: now + 2000, mode: "smoke" }];
    later(5000, () => {
      if (S.settings.jev_enabled) {
        pushEvent({ topic: "vision.events", source: "vision.jev_smoke", line: "welding", machine_id: "WLD-L1-RB01", camera_id: "CAM-WLD-01", type: "jev_vision", payload: { p_smoke: 0.87, box: [470, 150, 830, 430], latency_ms: 880 }, trace_id: null });
        addCost("jev");
        addCost("jev");
        S.handled.jev++;
      } else S.handled.rules_only++;
      const inc = makeIncident({ line: "welding", machine_id: "WLD-L1-RB01", camera_id: "CAM-WLD-01", type: "weld_smoke", title: "銲接煙霧異常", category: "equipment", rule_id: "R-WLD-SMOKE", severity: "S3", facts: { p_smoke_last2: [0.84, 0.87], motor_temp_c: 78 }, jev_box: [470, 150, 830, 430],
        diagnosis: S.settings.jev_enabled ? { incident_id: "", root_cause_top3: [ { failure_mode_id: "FM-WLD-EXHAUST", name: "排煙風機效能下降", p: 0.49 }, { failure_mode_id: "FM-WLD-GAS", name: "保護氣體流量不足", p: 0.3 }, { failure_mode_id: "FM-WLD-PARAM", name: "銲接電流設定過高", p: 0.14 } ], confidence: 0.66, mode: "jev_top3", explanation: "煙霧集中在銲槍上方且溫度上升，排煙風機效能下降的可能性最高。", explanation_status: "done" } : null });
      audit(inc.trace_id, "rules", "rule_hit", { rule_id: "R-WLD-SMOKE" });
      pushEvent({ topic: "alerts", source: "rules", line: "welding", machine_id: "WLD-L1-RB01", camera_id: "CAM-WLD-01", type: "incident_opened", payload: { incident_id: inc.id, severity: "S2", title: inc.title }, trace_id: inc.trace_id });
      emit("incidents", "created", inc);
      makeWorkorder(inc, { role: "technician", deadline_s: Math.min(15, S.settings.escalation_timeout_s) });
    });
    later(60000, () => {
      setSource("CAM-WLD-01", "assets/videos/welding/normal_01.mp4");
      S.segments["WLD-L1-RB01"] = [{ at: 0, mode: "normal" }];
    });
    return { ok: true };
  }
  throw new ApiError(404, `找不到劇本 ${id}`);
}

function setSource(cam: string, video: string) {
  S.cameraSources[cam] = video;
  emit("events", "camera_source", { event_id: uid("EV"), ts: iso(), topic: "control", source: "control", line: null, machine_id: null, camera_id: cam, type: "camera_source", payload: { video }, trace_id: null } satisfies SentinelEvent);
}

// ---------------------------------------------------------------- 維修 Agent（腳本化回覆）
const LOTO_ONLY_NOTE = "工單尚未完成 LOTO，依系統規則我現在只能提供上鎖清單與準備步驟。";

function agentReply(wo: WorkOrderDetail, userText: string) {
  const turns = (S.agentTurns[wo.id] ?? 0) + 1;
  S.agentTurns[wo.id] = turns;
  let content = "";
  let steps: string[] = [];
  let flags: SafetyFlag[] = [];
  let tools: AgentMessage["tool_calls"] = [{ name: "get_workorder" }];
  const top = wo.diagnosis?.root_cause_top3 ?? [];

  if (wo.category === "safety") {
    content = "這是工安類工單，不需要 LOTO。依 SOP-SAF-003：";
    steps = SOP_PPE[0].steps;
    tools.push({ name: "get_sop", args: { failure_mode_id: "SAF-PPE", phase: "respond" } });
  } else if (wo.status === "ASSIGNED" || wo.status === "ESCALATED" || wo.status === "OPEN") {
    content = "請先接單，接單後我會帶你完成 LOTO。";
  } else if (wo.status === "ACKED" || wo.status === "PERMIT_REQUESTED") {
    content = "依系統規則，作業許可核准、LOTO 完成前我只能協助準備。請先申請作業許可（需班長核准），準備：";
    steps = ["確認危害防止計畫與作業許可單內容（設施規則第57條）", "準備塞規、分厘卡、取料夾具", wo.status === "ACKED" ? "在上方按「申請作業許可」" : "等待班長核准作業許可"];
  } else if (wo.status === "PERMIT_APPROVED") {
    content = LOTO_ONLY_NOTE + `（${wo.loto?.loto_id ?? "LOTO"}）尚未完成的步驟：`;
    steps = (wo.loto?.steps ?? []).filter((s) => !s.checked_by).map((s) => `【${s.energy}】${s.text}`);
    steps.push("準備工具：塞規、分厘卡、取料夾具");
  } else {
    const repairTurn = (S.agentTurns[`${wo.id}:repair`] ?? 0) + 1;
    S.agentTurns[`${wo.id}:repair`] = repairTurn;
    if (wo.incident.type !== "feed_jam") {
      content = `依根因排序，先排查「${top[0]?.name ?? "最可能原因"}」。`;
      steps = ["確認上鎖狀態", "依 SOP 檢查對應部位", "回報檢查結果"];
    } else if (repairTurn === 1) {
      const first = top[0]?.name ?? "送料滾輪磨損";
      const ref = wo.diagnosis?.similar_cases?.find((c): c is SimilarCase => typeof c !== "string" && !!c.just_approved);
      content =
        (ref ? `上次同一台送料機（${ref.case_id}）也是卡料，最後是${ref.final_root_cause_name ?? "已確認的根因"}，這次一併留意。` : "") +
        `依 Jev 根因排序，先排查第一名「${first}」（${((top[0]?.p ?? 0.52) * 100).toFixed(0)}%）。`;
      steps =
        first === "送料滾輪磨損"
          ? ["確認主電源 QF1 已上鎖、氣壓表歸零", "目視檢查上下送料滾輪表面是否有亮面磨耗或溝痕", "以塞規量測滾輪間隙，標準 ≤ 0.05 mm（SOP-STP-012 §3.2）", "回報量測結果"]
          : ["確認主電源 QF1 已上鎖、氣壓表歸零", "以分厘卡量測板材三點厚度，公差 2.00 ± 0.05 mm", "比對本批材料進料檢驗紀錄", "回報量測結果"];
      tools = [{ name: "get_workorder" }, { name: "get_sop", args: { failure_mode_id: top[0]?.failure_mode_id, phase: "inspect" } }, { name: "get_machine_signals", args: { signal: "feed_length_error_mm", last_min: 10 } }];
    } else if (repairTurn === 2) {
      content = `了解，第一個方向排除。改排查第二名「${top[1]?.name ?? "材料厚度偏差"}」（${((top[1]?.p ?? 0.31) * 100).toFixed(0)}%），相似案例 CASE-STP-0087 也是板厚超差造成卡料。`;
      steps = ["從送料口側將卡住的板材退出", "伸手進模具區取出卡住的板材", "以分厘卡量測板材三點厚度，公差 2.00 ± 0.05 mm", "比對本批材料進料檢驗紀錄"];
      flags = [{ step_index: 1, step_text: steps[1], probs: { needs_loto: 0.2, enters_danger_zone: 0.9, high_voltage: 0.05, energized: 0.12 }, max_p: 0.9, reasons: ["enters_danger_zone"], requires_supervisor: true, supervisor_requested: true, source: "jev" }];
      tools = [{ name: "search_cases", args: { query: "送料卡料 板厚" } }, { name: "get_sop", args: { failure_mode_id: "FM-STP-MATERIAL-THICK", phase: "inspect" } }, { name: "request_supervisor", args: { reason: "步驟 2 涉及進入模具危險區" } }];
    } else if (repairTurn === 3) {
      content = "板厚 2.08 mm 超出公差，根因確認為「材料厚度偏差」。處置步驟：";
      steps = ["隔離本批材料並掛「待驗」標示", "換上合格批材料，依 SOP-STP-012 §4 重新設定送料長度", "試沖 10 模確認送料誤差 < 0.05 mm", "完成 Checklist 後，請由原上鎖人依 LOTO 解除程序執行"];
      tools = [{ name: "record_step", args: { step: "量測板厚", result: "2.08 mm" } }, { name: "get_sop", args: { failure_mode_id: "FM-STP-MATERIAL-THICK", phase: "repair" } }];
    } else {
      content = `已記錄：「${userText.slice(0, 30)}」。完成右側 Checklist 後即可提交結案回報，回報會經 Jev 檢查完整性再寫回知識庫（draft）。`;
      tools = [{ name: "record_step", args: { step: "技術員回報", result: userText.slice(0, 30) } }];
    }
  }

  const msg: AgentMessage = {
    id: uid("MSG"),
    workorder_id: wo.id,
    role: "assistant",
    content,
    steps,
    tool_calls: tools,
    safety_flags: [],
    ts: iso(),
  };
  const ch = `agent:${wo.id}`;
  const full = content + (steps.length ? "\n" + steps.map((s, i) => `${i + 1}. ${s}`).join("\n") : "");
  const n = Math.ceil(full.length / 6);
  const t0 = 500;
  for (let i = 0; i < n; i++) later(t0 + i * 30, () => emit(ch, "delta", { message_id: msg.id, text: full.slice(0, (i + 1) * 6) }));
  later(t0 + n * 30 + 80, () => {
    // 安全把關（J4）：在回覆送出前檢查每個步驟
    addCost("jev");
    if (flags.length) {
      msg.safety_flags = flags;
      audit(wo.trace_id ?? null, "jev", "safety_gate_flag", { workorder_id: wo.id, step: flags[0].step_text, probs: flags[0].probs });
      audit(wo.trace_id ?? null, "system", "request_supervisor", { workorder_id: wo.id, to: "王建國（主管）", reason: "安全把關 > 0.3" });
    }
    wo.agent_messages = [...(wo.agent_messages ?? []), msg];
    addCost("llm");
    audit(wo.trace_id ?? null, "agent", "agent_reply", { workorder_id: wo.id, tools: tools?.map((t) => t.name), safety_flags: flags.length });
    emit(ch, "message", msg);
    if (flags.length) {
      emit(ch, "safety_gate", { message_id: msg.id, flags });
      const sys: AgentMessage = {
        id: uid("MSG"),
        workorder_id: wo.id,
        role: "system",
        content: `安全把關：步驟 ${flags[0].step_index + 1}「${flags[0].step_text}」進入危險區機率 ${(flags[0].max_p * 100).toFixed(0)}%，已標紅並通知主管確認。請改用取料夾具，由送料口側退出板材。`,
        ts: iso(),
      };
      wo.agent_messages.push(sys);
      emit(ch, "message", sys);
    }
  });
  return msg.id;
}

// ---------------------------------------------------------------- 效益
function computeSummary(): MetricsSummary {
  const wos = [...S.workorders.values()];
  const acked = wos.filter((w) => w.acked_at);
  const arrive = acked.length ? acked.reduce((a, w) => a + (new Date(w.acked_at!).getTime() - new Date(w.created_at).getTime()) / 1000, 0) / acked.length : 0;
  const closed = wos.filter((w) => w.status === "CLOSED" && w.closed_at && w.category !== "safety");
  const mttr = closed.length ? closed.reduce((a, w) => a + (new Date(w.closed_at!).getTime() - new Date(w.created_at).getTime()) / 60000, 0) / closed.length : 0;
  const sev: Partial<Record<Severity, number>> = { S1: 0, S2: 0, S3: 0 };
  for (const i of S.incidents.values()) sev[i.final_severity] = (sev[i.final_severity] ?? 0) + 1;
  return {
    reaction_s: { detect: 0.9, notify: 0.74, arrive: +arrive.toFixed(1) },
    mttr_min: +mttr.toFixed(1),
    ai_cost_usd: +S.costTotal.toFixed(4),
    ai_cost_series: (() => {
      let acc = 0;
      return [...S.cost].sort((a, b) => a.ts.localeCompare(b.ts)).map((x) => ({ ts: x.ts, cost: +(acc += x.cost).toFixed(5) }));
    })(),
    handled: { ...S.handled },
    severity_counts: sev,
    incidents_total: S.incidents.size,
    open_workorders: wos.filter((w) => !["CLOSED", "CANCELLED"].includes(w.status)).length,
    comparison: [
      { method: "規則引擎（CEP）", latency_ms: 3, accuracy: 0.71, cost_per_call_usd: 0, calls: S.handled.rules_only + S.handled.jev + S.handled.llm },
      { method: "Jev 1.14（直覺層）", latency_ms: 610, accuracy: 0.86, cost_per_call_usd: 0.0011, calls: S.calls.jev },
      { method: "DeepSeek V4.1（深度說明）", latency_ms: 1650, accuracy: 0.82, cost_per_call_usd: 0, calls: S.calls.llm },
    ],
  };
}

const CALIBRATION: Calibration = {
  bins: [
    { p_pred: 0.05, p_obs: 0.03, n: 41 },
    { p_pred: 0.15, p_obs: 0.12, n: 33 },
    { p_pred: 0.25, p_obs: 0.22, n: 27 },
    { p_pred: 0.35, p_obs: 0.38, n: 22 },
    { p_pred: 0.45, p_obs: 0.41, n: 19 },
    { p_pred: 0.55, p_obs: 0.58, n: 18 },
    { p_pred: 0.65, p_obs: 0.61, n: 21 },
    { p_pred: 0.75, p_obs: 0.73, n: 24 },
    { p_pred: 0.85, p_obs: 0.88, n: 29 },
    { p_pred: 0.95, p_obs: 0.93, n: 36 },
  ],
  brier: 0.112,
  ece: 0.031,
  n_total: 270,
};

// ---------------------------------------------------------------- 工廠狀態
function plantWithStatus(): Plant {
  const p = clone(PLANT);
  const open = [...S.incidents.values()].filter((i) => i.status === "open");
  const util: Record<string, number> = { "STP-L1-PR01": 0.86, "STP-L1-FD02": 0.88, "ASM-L1-WS01": 0.91, "ASM-L1-CV01": 0.94, "WLD-L1-RB01": 0.79, "WLD-L1-FX01": 0.83 };
  for (const l of p.lines) {
    for (const m of l.machines) {
      const last = S.history[m.id]?.at(-1);
      const inc = open.filter((i) => i.machine_id === m.id);
      const worst = inc.some((i) => i.final_severity !== "S1") ? "alarm" : inc.length ? "warn" : "ok";
      m.state = last?.state_code === 2 ? "alarm" : "running";
      m.health = m.state === "alarm" ? "alarm" : worst;
      m.utilization = Math.max(0, (util[m.id] ?? 0.85) - (m.state === "alarm" ? 0.12 : 0));
    }
    for (const c of l.cameras) c.source = S.cameraSources[c.id] ?? c.source;
  }
  return p;
}

// ---------------------------------------------------------------- REST 路由
function need(cond: unknown, status: number, msg: string): asserts cond {
  if (!cond) throw new ApiError(status, msg);
}

function getWo(id: string) {
  const wo = S.workorders.get(id);
  need(wo, 404, `找不到工單 ${id}`);
  return wo;
}

export async function mockRequest<T>(method: "GET" | "POST", path: string, body: unknown, persona: Persona): Promise<T> {
  start();
  await sleep(rnd(40, 120));
  const [p, qs] = path.split("?");
  const q = new URLSearchParams(qs ?? "");
  const b = (body ?? {}) as Record<string, unknown>;
  const seg = p.split("/").filter(Boolean).map(decodeURIComponent);
  const me = persona.personId;

  if (method === "POST" && persona.role === "judge" && seg[0] !== "videos") throw new ApiError(403, "評審為唯讀身分，無法操作");

  const r = <R>(v: R) => clone(v) as unknown as T;

  if (method === "GET") {
    if (p === "/plant") return r(plantWithStatus());
    if (seg[0] === "machines" && seg[2] === "signals") {
      const lastMin = Number(q.get("last_min") ?? 10);
      const since = Date.now() - lastMin * 60e3;
      return r({ machine_id: seg[1], points: (S.history[seg[1]] ?? []).filter((x) => new Date(x.ts).getTime() >= since) });
    }
    if (p === "/incidents")
      return r([...S.incidents.values()].sort((a, c) => c.opened_at.localeCompare(a.opened_at)).map((i) => ({ ...i, incident_report: S.reports.has(i.id) ? reportSummary(i.id) : null })));
    if (seg[0] === "incidents" && seg[2] === "report") {
      need(S.incidents.has(seg[1]), 404, "找不到異常");
      return r(reportStatus(seg[1]));
    }
    if (p === "/kb/cases") {
      const st = q.get("status");
      return r([...S.cases.values()].filter((c) => !st || c.status === st));
    }
    if (p === "/health")
      return r({ ok: true, detector_mode: "scripted", yolo: { weights: "models/ppe.pt", weights_exist: false, classes: null }, embedding_model: "（模擬）multilingual-e5-small", kb: { docs: 412, cases: 38 + [...S.cases.values()].filter((c) => c.status === "approved").length, allow_draft: false }, jev: { enabled: S.settings.jev_enabled, model: "jev-1.14" }, llm: { enabled: true, model: "deepseek-v4.1-flash" }, notifier: "console", workflow: { require_permit: true }, scenario: null } satisfies Health);
    if (seg[0] === "incidents" && seg[1]) {
      const inc = S.incidents.get(seg[1]);
      need(inc, 404, "找不到異常");
      return r(inc);
    }
    if (p === "/workorders") return r([...S.workorders.values()].sort((a, c) => c.created_at.localeCompare(a.created_at)).map(out));
    if (seg[0] === "workorders" && seg[1]) return r(out(getWo(seg[1])));
    if (p === "/audit") {
      const tr = q.get("trace_id");
      const woId = q.get("workorder_id");
      let trace = tr;
      if (!trace && woId) trace = S.workorders.get(woId)?.trace_id ?? null;
      const list = trace ? S.audit.filter((a) => a.trace_id === trace) : S.audit.slice(-120);
      return r([...list].sort((a, c) => a.ts.localeCompare(c.ts)));
    }
    if (p === "/metrics/summary") return r(computeSummary());
    if (p === "/metrics/calibration") return r(CALIBRATION);
    if (p === "/scenarios") return r(SCENARIOS);
    if (p === "/videos") return r(VIDEOS);
    if (p === "/demo/settings") return r(S.settings);
    throw new ApiError(404, `模擬後端沒有 ${path}`);
  }

  // ---- POST
  if (seg[0] === "workorders" && seg[1]) {
    const wo = getWo(seg[1]);
    const action = seg[2];
    const tr = wo.trace_id ?? null;
    if (action === "ack") {
      need(["OPEN", "ASSIGNED", "ESCALATED"].includes(wo.status), 409, "此工單目前不能接單");
      wo.acked_at = iso();
      wo.ack_deadline = null;
      wo.assignee_id = me;
      wo.assignee_name = persona.name;
      wo.assignee_role = persona.role;
      audit(tr, me, "ack", { workorder_id: wo.id, via: "web" });
      transition(wo, "ACKED", me, "接單");
      return r(out(wo));
    }
    if (action === "reassign") {
      need(persona.role === "leader" || persona.role === "supervisor", 403, "只有班長或主管能轉派");
      const to = PEOPLE.find((x) => x.id === b.to_person_id) ?? pickAssignee("technician", wo.line);
      wo.assignee_id = to.id;
      wo.assignee_name = to.name;
      wo.assignee_role = to.role;
      wo.ack_deadline = iso(Date.now() + S.settings.escalation_timeout_s * 1000);
      audit(tr, me, "reassign", { workorder_id: wo.id, to: to.name, reason: b.reason });
      transition(wo, "ASSIGNED", me, `轉派給 ${to.name}：${String(b.reason ?? "")}`);
      return r(out(wo));
    }
    if (action === "loto") {
      need(wo.loto, 400, "此工單不需要 LOTO");
      need(wo.status === "PERMIT_APPROVED", 409, `作業許可核准後才能勾選 LOTO（目前狀態：${wo.status}）`);
      const stepNo = Number(seg[3]);
      const next = wo.loto.steps.find((s) => !s.checked_by);
      need(next && next.step_no === stepNo, 409, "請依順序勾選 LOTO 步驟");
      next.checked_by = me;
      next.ts = iso();
      audit(tr, me, "loto_check", { workorder_id: wo.id, loto_id: wo.loto.loto_id, step_no: stepNo, text: next.text });
      if (wo.loto.steps.every((s) => s.checked_by)) {
        transition(wo, "LOTO_DONE", me, `${wo.loto.loto_id} 全部勾選完成`);
        transition(wo, "IN_REPAIR", me, "LOTO 完成並驗證零能量，開始維修");
        const sys: AgentMessage = { id: uid("MSG"), workorder_id: wo.id, role: "system", content: "LOTO 完成：維修 Agent 已開放排查與維修步驟（get_sop inspect／repair）。", ts: iso() };
        wo.agent_messages = [...(wo.agent_messages ?? []), sys];
        emit(`agent:${wo.id}`, "message", sys);
      } else emit("workorders", "updated", summary(wo));
      return r(out(wo));
    }
    if (action === "permit") {
      need(wo.loto, 400, "此工單不需要作業許可");
      const act = String(b.action ?? "request");
      if (act === "request") {
        need(wo.status === "ACKED", 409, `接單後才能申請作業許可（目前狀態：${wo.status}）`);
        wo.permit = { status: "requested", requested_by: me, requested_at: iso(), approved_by: null, ts: iso() };
        audit(tr, me, "permit_requested", { workorder_id: wo.id });
        transition(wo, "PERMIT_REQUESTED", me, "申請作業許可");
      } else {
        need(persona.role === "leader" || persona.role === "supervisor", 403, "班長以上才能核准／退回作業許可");
        need(wo.status === "PERMIT_REQUESTED", 409, "目前沒有待核准的作業許可申請");
        wo.permit = { ...(wo.permit ?? { requested_by: null, approved_by: null, ts: null }), ts: iso() };
        if (act === "approve") {
          wo.permit.status = "approved";
          wo.permit.approved_by = me;
          audit(tr, me, "permit_approved", { workorder_id: wo.id });
          transition(wo, "PERMIT_APPROVED", me, "作業許可核准");
        } else {
          wo.permit.status = "rejected";
          audit(tr, me, "permit_rejected", { workorder_id: wo.id });
          transition(wo, "ACKED", me, "作業許可退回");
        }
      }
      return r(out(wo));
    }
    if (action === "checklist") {
      const isSafety = wo.category === "safety";
      need(isSafety ? wo.status === "ACKED" : wo.status === "IN_REPAIR", 409, isSafety ? "請先接單" : "作業許可核准、進入維修後才能勾選 Checklist");
      const it = wo.checklist?.find((c) => c.item === seg[3]);
      need(it, 404, "找不到項目");
      it.checked_by = me;
      it.ts = iso();
      audit(tr, me, "checklist_check", { workorder_id: wo.id, item: it.label });
      if (!isSafety && wo.checklist!.every((c) => c.checked_by)) transition(wo, "PENDING_VERIFY", me, "Checklist 完成");
      else emit("workorders", "updated", summary(wo));
      return r(out(wo));
    }
    if (action === "report") {
      const isSafety = wo.category === "safety";
      need(isSafety ? wo.status === "ACKED" : wo.status === "PENDING_VERIFY", 409, isSafety ? "請先接單" : "Checklist 完成後才能提交回報");
      const text = String(b.text ?? "");
      const rc = (b.root_cause_confirmed as string | null) ?? null;
      const missing: string[] = [];
      if (!isSafety && !rc) missing.push("確認的根因");
      if (text.trim().length < 15) missing.push("處置步驟（描述太短）");
      if (!/(試沖|驗證|確認|恢復|正常|復位|宣導)/.test(text)) missing.push("驗證結果");
      addCost("jev");
      const check: WritebackCheck = { complete: missing.length === 0, missing, score: +(1 - missing.length * 0.3).toFixed(2), kb_status: missing.length === 0 ? "draft" : "rejected" };
      wo.report = { text, root_cause_confirmed: rc, submitted_by: me, submitted_at: iso(), check };
      audit(tr, "jev", "writeback_check", { purpose: "J5", ...check });
      if (check.complete) {
        if (!isSafety) {
          const case_id = `CASE-DRAFT-${wo.id}`;
          S.cases.set(case_id, { case_id, status: "draft", line: wo.line, machine_id: wo.machine_id, workorder_id: wo.id, event_type: wo.incident.type, final_root_cause: rc, first_hypothesis: wo.diagnosis?.root_cause_top3[0]?.failure_mode_id ?? null, hypothesis_correct: rc ? rc === wo.diagnosis?.root_cause_top3[0]?.failure_mode_id : null, report: text, closed_at: iso(), reviewed_by: null, reviewed_at: null });
          wo.writeback = { case_id, status: "draft", reviewed_by: null, approve_url: `/api/kb/cases/${case_id}/approve` };
          check.kb_status = "draft";
          audit(tr, "system", "case_draft_written", { workorder_id: wo.id, case_id });
        }
        wo.closed_at = iso();
        wo.incident.status = "closed";
        wo.incident.closed_at = iso();
        const inc = S.incidents.get(wo.incident_id);
        if (inc) {
          inc.status = "closed";
          inc.closed_at = iso();
        }
        if (wo.incident.type === "feed_jam") {
          S.segments["STP-L1-FD02"] = [{ at: 0, mode: "normal" }];
          // 主管「核准入庫」後下一次卡料的根因機率才會變（見 /kb/cases/{id}/approve）
        }
        transition(wo, "CLOSED", me, "回報通過 J5 完整性檢查");
        emit("incidents", "updated", inc);
      } else emit("workorders", "updated", summary(wo));
      return r({ check, workorder: out(wo) });
    }
    if (action === "agent") {
      const text = String(b.message ?? "").trim();
      need(text, 400, "訊息不能是空的");
      const um: AgentMessage = { id: uid("MSG"), workorder_id: wo.id, role: "user", content: text, ts: iso() };
      wo.agent_messages = [...(wo.agent_messages ?? []), um];
      emit(`agent:${wo.id}`, "message", um);
      const mid = agentReply(wo, text);
      return r({ accepted: true, message_id: mid });
    }
  }
  if (seg[0] === "incidents" && seg[1]) {
    need(persona.role === "supervisor", 403, "需要組長（supervisor）以上身分");
    if (seg[2] === "casualty")
      return r(setCasualty(seg[1], { deaths: Number(b.deaths ?? 0), injured: Number(b.injured ?? 0), hospitalized: Number(b.hospitalized ?? 0), designated: !!b.designated, note: String(b.note ?? "") }, me));
    if (seg[2] === "report" && seg[3] === "submit") {
      const rep = S.reports.get(seg[1]);
      need(rep, 409, "此事件不符合職災通報條件，沒有通報草稿");
      need(!rep.submitted_at, 409, "通報已送出");
      if (b.draft) rep.draft = { ...rep.draft, ...(b.draft as Record<string, unknown>) };
      rep.submitted_by = me;
      rep.submitted_at = iso();
      const inc = S.incidents.get(seg[1])!;
      inc.incident_report = reportSummary(seg[1]);
      audit(inc.trace_id, me, "incident_report_submitted", { incident_id: seg[1] });
      emit("incidents", "report_submitted", { incident_id: seg[1], payload: reportStatus(seg[1]) });
      return r(reportStatus(seg[1]));
    }
  }
  if (seg[0] === "kb" && seg[1] === "cases" && seg[3] === "approve") {
    need(persona.role === "supervisor", 403, "案例入庫需要組長（supervisor）以上核准");
    const c = S.cases.get(seg[2]);
    need(c, 404, `找不到案例 ${seg[2]}`);
    need(c.status !== "approved", 409, `${seg[2]} 已經核准入庫`);
    c.status = "approved";
    c.reviewed_by = me;
    c.reviewed_at = iso();
    c.reviewed_at = iso(); // 下一次卡料判斷會在相似案例中引用它（機率不一定變）
    const wo = c.workorder_id ? S.workorders.get(c.workorder_id) : undefined;
    if (wo?.writeback) wo.writeback = { ...wo.writeback, status: "approved", reviewed_by: me };
    audit(wo?.trace_id ?? null, me, "case_approved", { case_id: c.case_id, workorder_id: c.workorder_id, chunks: 3 });
    if (wo) emit("workorders", "updated", summary(wo));
    emit("ai", "case_approved", { case_id: c.case_id, workorder_id: c.workorder_id, chunks: 3 });
    return r({ ok: true, case: c, ingested_chunks: 3 });
  }
  if (p === "/overrides") {
    need(persona.role === "supervisor", 403, "只有主管能覆寫");
    const target = String(b.target);
    const id = String(b.target_id);
    const field = String(b.field);
    if (target === "workorder") {
      const wo = getWo(id);
      if (field === "false_alarm") {
        wo.incident.status = "cancelled";
        const inc = S.incidents.get(wo.incident_id);
        if (inc) inc.status = "cancelled";
        wo.closed_at = iso();
        audit(wo.trace_id ?? null, me, "override", { target, id, field, new: true, reason: b.reason });
        transition(wo, "CANCELLED", me, `主管標記誤報：${String(b.reason ?? "")}`);
      } else if (field === "safety_ack") {
        const m = wo.agent_messages?.find((x) => x.id === b.new);
        m?.safety_flags?.forEach((f) => {
          f.supervisor_requested = false;
          f.acked_by = me;
          f.ack_reason = String(b.reason ?? "");
        });
        if (m) emit(`agent:${wo.id}`, "safety_gate", { message_id: m.id, flags: m.safety_flags });
        audit(wo.trace_id ?? null, me, "override", { target, id, field, message_id: b.new, reason: b.reason });
        const sys: AgentMessage = { id: uid("MSG"), workorder_id: wo.id, role: "system", content: `主管 ${persona.name} 已確認安全把關：${String(b.reason ?? "改用取料夾具，禁止徒手進入模具區")}`, ts: iso() };
        wo.agent_messages = [...(wo.agent_messages ?? []), sys];
        emit(`agent:${wo.id}`, "message", sys);
      } else if (field === "final_severity") {
        const old = wo.severity;
        wo.severity = b.new as Severity;
        wo.incident.final_severity = b.new as Severity;
        const inc = S.incidents.get(wo.incident_id);
        if (inc) inc.final_severity = b.new as Severity;
        audit(wo.trace_id ?? null, me, "override", { target, id, field, old, new: b.new, reason: b.reason });
        emit("workorders", "updated", summary(wo));
      }
      return r({ ok: true });
    }
    throw new ApiError(400, "不支援的覆寫目標");
  }
  if (seg[0] === "scenarios" && seg[2] === "run") return r(runScenario(seg[1]));
  if (p === "/demo/reset") {
    S.timers.forEach(clearTimeout);
    S.timers = [];
    S.learned = false;
    seed(false);
    emit("workorders", "reset", {});
    emit("incidents", "reset", {});
    emit("metrics", "summary", computeSummary());
    return r({ ok: true });
  }
  if (seg[0] === "cameras" && seg[2] === "source") {
    const video = String(b.video ?? "");
    setSource(seg[1], video);
    // 切到異常影片 → 自動觸發對應流程（攝影機模擬器的行為）
    if (video.includes("no_helmet")) runScenario("ppe_helmet");
    else if (video.includes("feed_jam")) runScenario("feed_jam");
    else if (video.includes("press_idle")) runScenario("idle_mismatch");
    else if (video.includes("smoke")) runScenario("weld_smoke");
    return r({ ok: true });
  }
  if (p === "/demo/settings") {
    if (typeof b.escalation_timeout_s === "number") S.settings.escalation_timeout_s = Math.max(5, Math.min(600, b.escalation_timeout_s));
    if (typeof b.jev_enabled === "boolean") S.settings.jev_enabled = b.jev_enabled;
    audit(null, me, "demo_settings", { ...S.settings });
    return r(S.settings);
  }
  throw new ApiError(404, `模擬後端沒有 ${method} ${path}`);
}

// 讓 tree-shaking 不抱怨未使用
void tickTimer;
void metricsTimer;
