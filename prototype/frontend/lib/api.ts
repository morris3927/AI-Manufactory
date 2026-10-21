// REST client，對應 08_系統設計.md §7.1。
// NEXT_PUBLIC_MOCK=1 時改由 lib/mock 回應（同一組函式、同一種回應型別）。

import { API_BASE, MOCK } from "./config";
import { getPersona } from "./session";
import type {
  AgentSendResult,
  AuditEntry,
  Health,
  IncidentReportStatus,
  KbCase,
  Calibration,
  DemoSettings,
  Incident,
  MachineSignals,
  MetricsSummary,
  Plant,
  Scenario,
  SignalPoint,
  VideoAsset,
  WorkOrder,
  WorkOrderDetail,
  WritebackCheck,
} from "./types";

import { ApiError } from "./errors";
export { ApiError };

type Method = "GET" | "POST";

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const persona = getPersona();
  if (MOCK) {
    const { mockRequest } = await import("./mock/server");
    return mockRequest<T>(method, path, body, persona);
  }
  const res = await fetch(`${API_BASE}/api${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Role": persona.role,
      "X-Person-Id": persona.personId,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = (j.detail && (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail))) || msg;
    } catch {
      /* 非 JSON */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** 有些端點可能回 {items: [...]}，統一成陣列 */
function unwrapList<T>(v: T[] | { items: T[] } | null | undefined): T[] {
  if (!v) return [];
  return Array.isArray(v) ? v : (v.items ?? []);
}

/** 訊號接受兩種形狀：{points:[{ts, sig...}]} 或 {signals:{sig:[{ts,v}]}} */
function normalizeSignals(id: string, raw: unknown): MachineSignals {
  const r = raw as {
    points?: SignalPoint[];
    signals?: Record<string, { ts: string; v?: number; value?: number }[]>;
  };
  if (r?.points) return { machine_id: id, points: r.points };
  const byTs = new Map<string, SignalPoint>();
  for (const [sig, arr] of Object.entries(r?.signals ?? {})) {
    for (const p of arr) {
      const row = byTs.get(p.ts) ?? { ts: p.ts };
      row[sig] = (p.v ?? p.value ?? 0) as number;
      byTs.set(p.ts, row);
    }
  }
  return { machine_id: id, points: [...byTs.values()].sort((a, b) => a.ts.localeCompare(b.ts)) };
}

export const api = {
  // 工廠
  plant: () => request<Plant>("GET", "/plant"),
  cameraStreamUrl: (id: string): string | null => (MOCK ? null : `${API_BASE}/api/cameras/${encodeURIComponent(id)}/stream`),
  setCameraSource: (id: string, video: string) => request<{ ok: boolean }>("POST", `/cameras/${encodeURIComponent(id)}/source`, { video }),
  signals: async (machineId: string, lastMin = 10) =>
    normalizeSignals(machineId, await request<unknown>("GET", `/machines/${encodeURIComponent(machineId)}/signals?last_min=${lastMin}`)),

  // 異常
  incidents: async () => unwrapList(await request<Incident[] | { items: Incident[] }>("GET", "/incidents")),
  incident: (id: string) => request<Incident>("GET", `/incidents/${encodeURIComponent(id)}`),

  // 工單
  workorders: async () => unwrapList(await request<WorkOrder[] | { items: WorkOrder[] }>("GET", "/workorders")),
  workorder: (id: string) => request<WorkOrderDetail>("GET", `/workorders/${encodeURIComponent(id)}`),
  ack: (id: string) => request<WorkOrderDetail>("POST", `/workorders/${id}/ack`, {}),
  reassign: (id: string, to_person_id: string | null, reason: string) =>
    request<WorkOrderDetail>("POST", `/workorders/${id}/reassign`, { to_person_id, reason }),
  checkLoto: (id: string, step: number) => request<WorkOrderDetail>("POST", `/workorders/${id}/loto/${step}`, { checked: true }),
  permit: (id: string, action: "request" | "approve" | "reject") => request<WorkOrderDetail>("POST", `/workorders/${id}/permit`, { action }),
  checkItem: (id: string, item: string) =>
    request<WorkOrderDetail>("POST", `/workorders/${id}/checklist/${encodeURIComponent(item)}`, { checked: true }),
  report: (id: string, text: string, root_cause_confirmed: string | null) =>
    request<{ check: WritebackCheck; workorder?: WorkOrderDetail }>("POST", `/workorders/${id}/report`, { text, root_cause_confirmed }),
  /** wait=true：同步等 Agent 回覆（後端也會同時推 WS agent:{id}） */
  sendAgent: (id: string, message: string) => request<AgentSendResult>("POST", `/workorders/${id}/agent?wait=true`, { message, wait: true }),

  // 職災 8 小時通報（職安法第37條第2項）
  casualty: (incidentId: string, c: { deaths: number; injured: number; hospitalized: number; designated: boolean; note: string }) =>
    request<IncidentReportStatus>("POST", `/incidents/${encodeURIComponent(incidentId)}/casualty`, c),
  incidentReport: (incidentId: string) => request<IncidentReportStatus>("GET", `/incidents/${encodeURIComponent(incidentId)}/report`),
  submitIncidentReport: (incidentId: string, draft?: Record<string, unknown>) =>
    request<IncidentReportStatus>("POST", `/incidents/${encodeURIComponent(incidentId)}/report/submit`, draft ? { draft } : {}),

  // 知識回寫審核
  kbCases: async (status?: "draft" | "approved") => unwrapList(await request<KbCase[] | { items: KbCase[] }>("GET", `/kb/cases${status ? `?status=${status}` : ""}`)),
  approveCase: (caseId: string, note?: string) =>
    request<{ ok: boolean; case: KbCase; ingested_chunks: number }>("POST", `/kb/cases/${encodeURIComponent(caseId)}/approve`, note ? { note } : {}),

  // 系統狀態
  health: () => request<Health>("GET", "/health"),

  // 主管覆寫
  override: (o: { target: "incident" | "workorder"; target_id: string; field: string; new: unknown; reason: string }) =>
    request<{ ok: boolean }>("POST", "/overrides", o),

  // 稽核
  audit: async (q: { trace_id?: string; workorder_id?: string }) => {
    const p = new URLSearchParams();
    if (q.trace_id) p.set("trace_id", q.trace_id);
    if (q.workorder_id) p.set("workorder_id", q.workorder_id);
    return unwrapList(await request<AuditEntry[] | { items: AuditEntry[] }>("GET", `/audit?${p}`));
  },

  // 效益
  metricsSummary: () => request<MetricsSummary>("GET", "/metrics/summary"),
  calibration: () => request<Calibration>("GET", "/metrics/calibration"),

  // 控制台
  scenarios: async () => unwrapList(await request<Scenario[] | { items: Scenario[] }>("GET", "/scenarios")),
  runScenario: (id: string) => request<{ ok: boolean; trace_id?: string; deduped?: boolean }>("POST", `/scenarios/${id}/run`, {}),
  resetDemo: () => request<{ ok: boolean }>("POST", "/demo/reset", {}),
  // 以下兩組不在 §7.1 表內（假設的端點），失敗時前端會顯示提示
  videos: async () => unwrapList(await request<VideoAsset[] | { items: VideoAsset[] }>("GET", "/videos")),
  settings: () => request<DemoSettings>("GET", "/demo/settings"),
  updateSettings: (s: Partial<DemoSettings>) => request<DemoSettings>("POST", "/demo/settings", s),
};

export type Api = typeof api;
