"use client";

import { ChevronRight } from "lucide-react";
import { STATUS_LABEL } from "@/lib/labels";
import type { AuditEntry, WorkOrderStatus } from "@/lib/types";
import { cn, fmtDateTime } from "@/lib/utils";

const ACTION_LABEL: Record<string, string> = {
  rule_hit: "規則命中",
  routed: "分流派工",
  jev_judgement: "Jev 事件判斷",
  jev_event_judgement: "Jev 事件判斷",
  jev_vision_feed: "Jev 看圖（送料）",
  jev_vision_ppe: "Jev 看圖（PPE）",
  jev_unavailable: "Jev 不可用（退路）",
  workorder_created: "建立工單",
  workorder_transition: "工單狀態變更",
  notified: "已通知",
  notify_discord: "已通知",
  notify_suppressed: "通知抑制（疑似誤報）",
  llm_explanation: "LLM 說明",
  llm_unavailable: "LLM 不可用",
  loto_step_checked: "LOTO 勾選",
  loto_check: "LOTO 勾選",
  permit_requested: "申請作業許可",
  permit_approved: "核准作業許可",
  checklist_checked: "Checklist 勾選",
  checklist_check: "Checklist 勾選",
  report_submitted: "提交結案回報",
  report_rejected: "回報退回（J5）",
  writeback_check: "回報完整性檢查（J5）",
  case_draft_written: "寫回知識庫（草稿）",
  kb_writeback: "寫回知識庫（草稿）",
  supervisor_requested: "要求主管確認",
  request_supervisor: "要求主管確認",
  agent_reply: "Agent 回覆",
  agent_tool: "Agent 查詢工具",
  safety_gate_flag: "安全把關標紅",
  override: "主管覆寫",
  ack: "接單",
  reassign: "轉派",
  scenario_started: "劇本啟動",
  camera_source_changed: "切換影片",
  demo_reset: "重置 Demo",
  demo_settings_changed: "Demo 設定",
  demo_settings: "Demo 設定",
  mes_anomaly: "MES 異常",
  similar_cases: "相似案例檢索",
  ppe_violation: "PPE 違規",
  new_failure_mode_suspected: "疑似新故障模式",
};

const ACTOR_STYLE: Record<string, string> = {
  jev: "bg-orange-500/20 text-orange-200",
  llm: "bg-emerald-500/20 text-emerald-200",
  rules: "bg-blue-500/20 text-blue-200",
  system: "bg-slate-500/25 text-slate-200",
};

type D = Record<string, unknown>;
const st = (s: unknown) => STATUS_LABEL[s as WorkOrderStatus] ?? String(s ?? "—");
const p2 = (v: unknown) => (typeof v === "number" ? (Math.abs(v) < 1 && v !== 0 ? v.toFixed(2) : String(Math.round(v * 1000) / 1000)) : String(v));
const short = (v: unknown, n = 60) => {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > n ? s.slice(0, n) + "…" : s;
};

/** 常見動作的易讀摘要；回傳 null 時用通用 key: value */
function summarize(a: AuditEntry): string | null {
  const d = (a.detail ?? {}) as D;
  switch (a.action) {
    case "rule_hit":
      return [`規則 ${d.rule_id ?? ""}`, d.entity ?? d.machine_id, d.severity, d.category].filter(Boolean).join("・");
    case "routed": {
      const p = (d.plan ?? {}) as D;
      return [
        `模式 ${p.mode ?? "—"}`,
        p.ai === undefined ? null : `AI ${p.ai ? "啟用" : "不呼叫"}`,
        p.assignee_id && `指派 ${p.assignee_id}（${p.assignee_role ?? ""}）`,
        p.channel && `#${p.channel}`,
        Array.isArray(p.cc) && p.cc.length ? `副本 ${p.cc.join("、")}` : null,
      ]
        .filter(Boolean)
        .join("・");
    }
    case "jev_judgement":
    case "jev_event_judgement": {
      const j = ((d.judgement as D) ?? d) as D;
      const top = (j.root_cause_top3 ?? j.top3) as unknown[] | undefined;
      const topTxt = (top ?? [])
        .map((r) => (typeof r === "string" ? r : `${(r as D).label ?? (r as D).name ?? (r as D).id} ${p2((r as D).p)}`))
        .join("、");
      return [j.jev_severity && `嚴重度 ${j.jev_severity}`, j.p_false_alarm !== undefined && `誤報機率 ${p2(j.p_false_alarm)}`, topTxt && `根因 ${topTxt}`, d.latency_ms && `${d.latency_ms} ms`]
        .filter(Boolean)
        .join("・");
    }
    case "workorder_transition":
      return `${d.workorder_id ?? ""} ${st(d.from)} → ${st(d.to)}${d.reason ? `（${d.reason}）` : ""}`;
    case "workorder_created":
      return [d.workorder_id, d.severity, d.category, d.assignee].filter(Boolean).join("・");
    case "notified":
    case "notify_discord":
      return [d.kind, d.channel && `#${String(d.channel).replace(/^#/, "")}`, d.notifier && `經 ${d.notifier}`, (d.recipient_id ?? d.to) && `給 ${d.recipient_id ?? d.to}`].filter(Boolean).join("・");
    case "llm_explanation":
      return [d.ok === false ? "失敗" : "完成", d.chars && `${d.chars} 字`, d.model, d.latency_ms && `${d.latency_ms} ms`].filter(Boolean).join("・");
    case "loto_step_checked":
    case "loto_check":
      return `${d.loto_id ?? ""} 第 ${d.step_no} 步${d.text ? `：${d.text}` : ""}`;
    case "permit_requested":
    case "permit_approved":
      return String(d.workorder_id ?? "");
    case "checklist_checked":
    case "checklist_check":
      return short(d.item, 50);
    case "report_submitted":
      return `${d.workorder_id ?? ""}・${d.chars ?? "?"} 字`;
    case "report_rejected":
    case "writeback_check":
      return [d.complete === true ? "完整" : d.complete === false ? "不完整" : null, Array.isArray(d.missing) && d.missing.length ? `缺 ${(d.missing as string[]).join("、")}` : null].filter(Boolean).join("・") || null;
    case "case_draft_written":
    case "kb_writeback":
      return String(d.case_id ?? "");
    case "supervisor_requested":
    case "request_supervisor":
      return [d.to && `給 ${d.to}`, d.reason && short(d.reason, 70)].filter(Boolean).join("・");
    case "agent_reply":
      return [d.phase && `階段 ${d.phase}`, d.llm_used !== undefined && (d.llm_used ? "LLM 產生" : "固定流程"), Array.isArray(d.safety_flags) && d.safety_flags.length ? `旗標 ${(d.safety_flags as string[]).join("、")}` : null, d.supervisor_requested ? "已要求主管確認" : null]
        .filter(Boolean)
        .join("・");
    case "override":
      return `${d.field}：${short(d.old ?? "—", 20)} → ${short(d.new, 20)}${d.reason ? `（${d.reason}）` : ""}`;
    case "agent_tool":
      return `${d.tool}(${d.args && Object.keys(d.args as D).length ? short(d.args, 50) : ""})`;
    default:
      return null;
  }
}

function KeyValues({ d }: { d: D }) {
  const entries = Object.entries(d).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object").slice(0, 6);
  if (!entries.length) return null;
  return (
    <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
      {entries.map(([k, v]) => (
        <span key={k}>
          <span className="text-muted">{k}</span> <span className="font-mono text-slate-200">{short(p2(v), 40)}</span>
        </span>
      ))}
    </div>
  );
}

function Raw({ d }: { d: unknown }) {
  return (
    <details className="group ml-auto open:ml-0 open:w-full">
      <summary className="flex cursor-pointer list-none items-center gap-0.5 text-xs text-muted hover:text-foreground">
        <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" /> 原始 JSON
      </summary>
      <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-black/40 p-2 font-mono text-xs text-slate-300">{JSON.stringify(d, null, 2)}</pre>
    </details>
  );
}

function Actor({ a }: { a: string }) {
  const k = a.startsWith("agent") ? "agent" : a;
  return <span className={cn("rounded px-1.5 py-px font-mono text-xs", ACTOR_STYLE[k] ?? (k === "agent" ? "bg-violet-500/20 text-violet-200" : "bg-cyan-500/15 text-cyan-200"))}>{a}</span>;
}

function Row({ a }: { a: AuditEntry }) {
  const sum = summarize(a);
  const d = (a.detail ?? {}) as D;
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-2 text-sm">
        <span className="tabular font-mono text-xs text-muted">{fmtDateTime(a.ts)}</span>
        <Actor a={a.actor} />
        <span className="font-medium text-slate-100">{ACTION_LABEL[a.action] ?? a.action}</span>
        {sum && <span className="text-slate-300">{sum}</span>}
        {Object.keys(d).length > 0 && <Raw d={d} />}
      </div>
      {!sum && <KeyValues d={d} />}
    </>
  );
}

type Item = { kind: "one"; a: AuditEntry } | { kind: "tools"; list: AuditEntry[] };

function group(list: AuditEntry[]): Item[] {
  const out: Item[] = [];
  for (const a of list) {
    const last = out.at(-1);
    if (a.action === "agent_tool") {
      if (last?.kind === "tools") last.list.push(a);
      else out.push({ kind: "tools", list: [a] });
    } else out.push({ kind: "one", a });
  }
  return out;
}

export function AuditList({ list, compact }: { list: AuditEntry[]; compact?: boolean }) {
  if (!list.length) return <div className="py-4 text-center text-sm text-muted">沒有紀錄</div>;
  return (
    <ol className={cn("relative space-y-2 border-l border-line pl-4", compact && "text-xs")}>
      {group(list).map((it, i) => (
        <li key={i} className="relative">
          <span className={cn("absolute -left-[20px] top-1.5 h-2 w-2 rounded-full", it.kind === "tools" ? "bg-violet-400" : "bg-cyan-400")} />
          {it.kind === "one" ? (
            <Row a={it.a} />
          ) : it.list.length === 1 ? (
            <Row a={it.list[0]} />
          ) : (
            <details className="group">
              <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-2 text-sm">
                <span className="tabular font-mono text-xs text-muted">{fmtDateTime(it.list[0].ts)}</span>
                <Actor a={it.list[0].actor} />
                <span className="font-medium text-slate-100">Agent 查詢工具 ×{it.list.length}</span>
                <span className="truncate text-slate-400">{[...new Set(it.list.map((x) => String((x.detail as D)?.tool ?? "")))].join("、")}</span>
                <ChevronRight className="h-3.5 w-3.5 self-center text-muted transition-transform group-open:rotate-90" />
              </summary>
              <ol className="mt-1 space-y-1 border-l border-violet-500/30 pl-3">
                {it.list.map((a, j) => (
                  <li key={j}>
                    <Row a={a} />
                  </li>
                ))}
              </ol>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}
