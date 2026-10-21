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
  | "PERMIT_REQUESTED"
  | "PERMIT_APPROVED"
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
  /** 判斷時引用的相似案例（後端若提供；字串或物件皆可） */
  similar_cases?: (string | SimilarCase)[] | null;
}

export interface SimilarCase {
  case_id: string;
  title?: string | null;
  final_root_cause?: string | null;
  final_root_cause_name?: string | null;
  score?: number | null;
  status?: string | null;
  /** 剛由主管核准入庫的案例 */
  just_approved?: boolean;
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
  casualty?: Casualty | null;
  /** 職災通報摘要（後端 incident_out 內嵌） */
  incident_report?: Pick<IncidentReportStatus, "status" | "deadline" | "remaining_s" | "criteria_met" | "instructions"> | null;
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
  status?: "requested" | "approved" | "rejected";
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
  requires_supervisor?: boolean;
  /** 超過門檻的題目（後端 J4 判定） */
  reasons?: string[];
  source?: string;
  acked_by?: string | null;
  ack_reason?: string | null;
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
  workflow?: { require_permit: boolean; next_action: NextAction | null } | null;
  writeback?: { case_id: string; status: string | null; reviewed_by: string | null; approve_url?: string } | null;
  sop_mode?: string | null;
  sop_note?: string | null;
}

export type NextAction = "assign" | "ack" | "permit_request" | "permit_approve" | "loto" | "checklist" | "report";

// ---------- 職災 8 小時通報（職安法第37條第2項） ----------
export interface Casualty {
  deaths: number;
  injured: number;
  hospitalized: number;
  designated: boolean;
  note?: string;
  set_by?: string;
  set_at?: string;
}

export interface IncidentReportStatus {
  incident_id: string;
  required: boolean;
  criteria_met: string[];
  casualty: Casualty | null;
  status: "none" | "draft" | "overdue" | "submitted";
  deadline: string | null;
  remaining_s: number | null;
  draft: Record<string, unknown> | null;
  instructions: string[];
  submitted_by: string | null;
  submitted_at: string | null;
}

// ---------- 知識回寫 ----------
export interface KbCase {
  case_id: string;
  status: string | null;
  line?: string | null;
  machine_id?: string | null;
  workorder_id?: string | null;
  event_type?: string | null;
  final_root_cause?: string | null;
  first_hypothesis?: string | null;
  hypothesis_correct?: boolean | null;
  report?: string | null;
  closed_at?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
}

// ---------- 系統狀態 ----------
export interface Health {
  ok: boolean;
  detector_mode: "yolo" | "scripted" | string;
  yolo?: { weights?: string; weights_exist?: boolean; classes?: string[] | null };
  embedding_model?: string | null;
  kb?: { docs: number; cases: number; allow_draft?: boolean };
  jev?: { enabled: boolean; model?: string };
  llm?: { enabled: boolean; model?: string };
  notifier?: string;
  workflow?: { require_permit: boolean };
  scenario?: string | null;
}

export interface AgentSendResult {
  accepted: boolean;
  message_id?: string | number | null;
  user_message_id?: string | number | null;
  message?: AgentMessage | null;
  phase?: string;
  supervisor_requested?: boolean;
  safety_flags?: SafetyFlag[];
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
