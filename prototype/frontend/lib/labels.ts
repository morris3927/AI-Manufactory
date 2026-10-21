import type { Category, Role, Severity, WorkOrderStatus } from "./types";

export const STATUS_LABEL: Record<WorkOrderStatus, string> = {
  OPEN: "待指派",
  ASSIGNED: "已指派",
  ESCALATED: "已升級",
  ACKED: "已接單",
  PERMIT_REQUESTED: "許可申請中",
  PERMIT_APPROVED: "許可已核准",
  LOTO_DONE: "LOTO 完成",
  IN_REPAIR: "維修中",
  PENDING_VERIFY: "待確認",
  CLOSED: "已結案",
  CANCELLED: "誤報取消",
};

export type KanbanColumn = "todo" | "doing" | "verify" | "done";

export const KANBAN_COLUMNS: { id: KanbanColumn; label: string; statuses: WorkOrderStatus[] }[] = [
  { id: "todo", label: "待處理", statuses: ["OPEN", "ASSIGNED", "ESCALATED"] },
  { id: "doing", label: "處理中", statuses: ["ACKED", "PERMIT_REQUESTED", "PERMIT_APPROVED", "LOTO_DONE", "IN_REPAIR"] },
  { id: "verify", label: "待確認", statuses: ["PENDING_VERIFY"] },
  { id: "done", label: "已解除", statuses: ["CLOSED", "CANCELLED"] },
];

export function kanbanColumnOf(s: WorkOrderStatus): KanbanColumn {
  return KANBAN_COLUMNS.find((c) => c.statuses.includes(s))?.id ?? "todo";
}

/** 狀態在流程中的順序，用來判斷「是否已到達某狀態」 */
const ORDER: WorkOrderStatus[] = [
  "OPEN",
  "ASSIGNED",
  "ESCALATED",
  "ACKED",
  "PERMIT_REQUESTED",
  "PERMIT_APPROVED",
  "LOTO_DONE",
  "IN_REPAIR",
  "PENDING_VERIFY",
  "CLOSED",
];

export function reached(s: WorkOrderStatus, target: WorkOrderStatus): boolean {
  if (s === "CANCELLED") return false;
  return ORDER.indexOf(s) >= ORDER.indexOf(target);
}

export const ROLE_LABEL: Record<Role, string> = {
  technician: "技術員",
  leader: "班長",
  supervisor: "主管",
  judge: "評審（唯讀）",
};

/** 人員職位（roster.csv 的 role） */
export const PERSON_ROLE_LABEL: Record<string, string> = {
  operator: "作業員",
  technician: "技術員",
  leader: "班長",
  supervisor: "組長",
  manager: "主管",
};

export const CATEGORY_LABEL: Record<Category, string> = {
  safety: "工安",
  equipment: "設備",
  quality: "品質",
  process: "製程",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  S1: "S1 輕微",
  S2: "S2 重要",
  S3: "S3 緊急",
};

export const SEVERITY_STYLE: Record<Severity, { bar: string; text: string; bg: string; hex: string }> = {
  S1: { bar: "bg-sky-400", text: "text-sky-300", bg: "bg-sky-500/15 border-sky-500/40", hex: "#38bdf8" },
  S2: { bar: "bg-amber-400", text: "text-amber-300", bg: "bg-amber-500/15 border-amber-500/40", hex: "#fbbf24" },
  S3: { bar: "bg-red-500", text: "text-red-300", bg: "bg-red-500/15 border-red-500/50", hex: "#ef4444" },
};

export const LINE_LABEL: Record<string, string> = {
  stamping: "沖壓線",
  assembly: "組裝線",
  welding: "銲接線",
};

export const EVENT_TYPE_LABEL: Record<string, string> = {
  ppe_violation: "PPE 違規",
  machine_state: "機台狀態",
  jev_vision: "Jev 看圖",
  mes_log: "MES Log",
  feed_rate: "送料速率",
  incident_opened: "異常成立",
  camera_offline: "訊號中斷",
  scenario_started: "劇本啟動",
};
