// 與 02_規格定義/08_系統設計.md §4.2、§5.8、§6、§7 對應的型別。
// 設計文件沒寫死的回應欄位，以「可選欄位」表示，見 README 的「API 假設」。

export type Severity = "S1" | "S2" | "S3";
export type Category = "safety" | "equipment" | "quality" | "process";

export type Role = "technician" | "leader" | "supervisor" | "judge";

export type WorkOrderStatus =
  | "OPEN"
  | "ASSIGNED"
  | "ESCALATED"
  | "ACKED"
  | "LOTO_DONE"
  | "IN_REPAIR"
  | "PENDING_VERIFY"
  | "CLOSED"
  | "CANCELLED";

/** x1, y1, x2, y2，以 1280×720 為準 */
export type Box = [number, number, number, number];

// ---------- /api/plant ----------
export interface Machine {
  id: string;
  type: string;
  name: string;
  line?: string;
  /** 假設：後端可附上即時狀態；沒有時前端由 events 推算 */
  state?: "running" | "idle" | "alarm" | "offline";
  health?: "ok" | "warn" | "alarm" | "offline";
  utilization?: number; // 0–1
}

export interface Camera {
  id: string;
  machine_id?: string | null;
  rois?: Record<string, Box>;
  detectors?: string[];
  name?: string;
  line?: string;
  /** 假設：目前播放的影片 */
  source?: string | null;
}

export interface Line {
  id: string;
  name: string;
  machines: Machine[];
  cameras: Camera[];
}

export interface Plant {
  lines: Line[];
}

// ---------- 事件（§4.2） ----------
export interface SentinelEvent {
  event_id: string;
  ts: string;
  topic: string;
  source: string;
  line: string | null;
  machine_id: string | null;
  camera_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  trace_id: string | null;
}

// ---------- Incident ----------
export interface RootCause {
  failure_mode_id: string;
  name: string;
  p: number;
}

export interface Diagnosis {
  incident_id: string;
  root_cause_top3: RootCause[];
  confidence: number;
  mode: "jev_top3" | "llm_deep" | "rules_only";
  explanation?: string | null;
  /** 假設：LLM 說明狀態 */
  explanation_status?: "pending" | "streaming" | "done" | "failed";
  p_false_alarm?: number;
  questions_ver?: string;
}

export interface Incident {
  id: string;
  trace_id: string;
  line: string;
  machine_id: string | null;
  camera_id?: string | null;
  type: string;
  title?: string;
  category: Category;
  rule_id: string;
  rule_severity: Severity;
  final_severity: Severity;
  facts: Record<string, unknown>;
  status: string;
  opened_at: string;
  closed_at: string | null;
  suspected_false_alarm?: boolean;
  /** 假設：關鍵幀圖片位址與 Jev Box */
  keyframe_url?: string | null;
  jev_box?: Box | null;
  diagnosis?: Diagnosis | null;
}

// ---------- WorkOrder ----------
export interface Person {
  id: string;
  name: string;
  role: string;
  line?: string;
}

export interface LotoStep {
  step_no: number;
  text: string;
  energy?: string;
  checked_by: string | null;
  ts: string | null;
}

export interface LotoChecklist {
  loto_id: string;
  machine_type?: string;
  title?: string;
  steps: LotoStep[];
}

export interface Permit {
  requested_by: string | null;
  requested_at?: string | null;
  approved_by: string | null;
  ts: string | null;
}

export interface ChecklistItem {
  item: string;
  label: string;
  checked_by: string | null;
  ts: string | null;
}

export interface SopChunk {
  sop_id: string;
  title: string;
  phase: string;
  failure_mode_id?: string;
  steps: string[];
  source?: string;
}

export interface SafetyFlag {
  step_index: number;
  step_text: string;
  /** 例如 { enters_danger_zone: 0.9 } */
  probs: Record<string, number>;
  max_p: number;
  supervisor_requested?: boolean;
}

export interface AgentMessage {
  id: string;
  workorder_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  steps?: string[];
  tool_calls?: { name: string; args?: Record<string, unknown> }[];
  safety_flags?: SafetyFlag[];
  ts: string;
  streaming?: boolean;
}

export interface Transition {
  from: WorkOrderStatus | null;
  to: WorkOrderStatus;
  actor: string;
  reason?: string | null;
  ts: string;
}

export interface WritebackCheck {
  complete: boolean;
  missing: string[];
  score?: number;
  kb_status?: "draft" | "rejected" | "approved";
}

export interface ClosingReport {
  text: string;
  root_cause_confirmed?: string | null;
  submitted_by: string | null;
  submitted_at: string | null;
  check?: WritebackCheck | null;
}

export interface WorkOrder {
  id: string;
  incident_id: string;
  status: WorkOrderStatus;
  assignee_id: string | null;
  assignee_name?: string | null;
  assignee_role: string | null;
  escalation_level: number;
  ack_deadline: string | null;
  created_at: string;
  acked_at: string | null;
  closed_at: string | null;
  /** 列表常用的 Incident 摘要（假設：列表回應會內嵌） */
  line?: string;
  machine_id?: string | null;
  severity?: Severity;
  category?: Category;
  title?: string;
  trace_id?: string;
}

export interface WorkOrderDetail extends WorkOrder {
  incident: Incident;
  diagnosis?: Diagnosis | null;
  sops?: SopChunk[];
  loto?: LotoChecklist | null;
  permit?: Permit | null;
  checklist?: ChecklistItem[];
  agent_messages?: AgentMessage[];
  transitions?: Transition[];
  report?: ClosingReport | null;
}

// ---------- 訊號 ----------
export interface SignalPoint {
  ts: string;
  [signal: string]: number | string;
}

export interface MachineSignals {
  machine_id: string;
  points: SignalPoint[];
}

// ---------- 稽核 ----------
export interface AuditEntry {
  ts: string;
  trace_id: string | null;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
}

// ---------- 效益 ----------
export interface MetricsSummary {
  reaction_s: { detect: number; notify: number; arrive: number };
  mttr_min: number;
  ai_cost_usd: number;
  ai_cost_series?: { ts: string; cost: number }[];
  handled: { rules_only: number; jev: number; llm: number };
  severity_counts?: Partial<Record<Severity, number>>;
  incidents_total?: number;
  open_workorders?: number;
  comparison?: {
    method: string;
    latency_ms: number;
    accuracy: number | null;
    cost_per_call_usd: number;
    calls: number;
  }[];
}

export interface CalibrationBin {
  p_pred: number;
  p_obs: number;
  n: number;
}

export interface Calibration {
  bins: CalibrationBin[];
  brier?: number;
  ece?: number;
  n_total?: number;
}

// ---------- 控制台 ----------
export interface Scenario {
  id: string;
  title: string;
  description?: string;
}

export interface VideoAsset {
  path: string;
  label: string;
  line?: string;
}

export interface DemoSettings {
  escalation_timeout_s: number;
  jev_enabled: boolean;
}

// ---------- WebSocket（§7.2） ----------
export type WsChannel =
  | "events"
  | "incidents"
  | "workorders"
  | "ai"
  | "metrics"
  | `agent:${string}`;

export interface WsMessage<T = unknown> {
  channel: WsChannel | string;
  type: string;
  data: T;
  ts: string;
}
