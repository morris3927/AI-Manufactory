"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertOctagon,
  ChevronRight,
  Bot,
  CheckCircle2,
  Circle,
  ClipboardCheck,
  FileText,
  History,
  KeyRound,
  Lock,
  Send,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import { CameraView, type Overlay } from "../camera-tile";
import { RootCauseBars, SignalCharts } from "../charts";
import { Countdown } from "../countdown";
import { SeverityBadge } from "../severity";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Badge } from "../ui/badge";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { CATEGORY_LABEL, LINE_LABEL, PERSON_ROLE_LABEL, STATUS_LABEL, reached } from "@/lib/labels";
import { usePersona } from "@/lib/session";
import type { AuditEntry, NextAction, Severity, WorkOrderDetail, WorkOrderStatus } from "@/lib/types";
import { cn, fmtTime, pct } from "@/lib/utils";
import type { Streaming } from "./use-workorder";
import { toast } from "../toast";
import { AssistantContent, ChatBubble, sortMessages } from "./agent-message";
import { AuditList } from "../audit-list";

type Act = <T>(fn: () => Promise<T>, okText?: string) => Promise<T | undefined>;

const isSafety = (wo: WorkOrderDetail) => wo.category === "safety" || wo.incident?.category === "safety";
const requirePermit = (wo: WorkOrderDetail) => wo.workflow?.require_permit ?? true;

/** 後端 workflow.next_action；舊版後端沒有時由狀態推算 */
export function nextAction(wo: WorkOrderDetail): NextAction | null {
  if (wo.workflow && "next_action" in wo.workflow) return wo.workflow.next_action;
  const s = wo.status;
  if (isSafety(wo)) return s === "ASSIGNED" || s === "ESCALATED" ? "ack" : s === "ACKED" ? "report" : null;
  const m: Partial<Record<WorkOrderStatus, NextAction>> = {
    ASSIGNED: "ack",
    ESCALATED: "ack",
    ACKED: requirePermit(wo) ? "permit_request" : "loto",
    PERMIT_REQUESTED: "permit_approve",
    PERMIT_APPROVED: "loto",
    LOTO_DONE: "loto",
    IN_REPAIR: "checklist",
    PENDING_VERIFY: "report",
  };
  return m[s] ?? null;
}

const NEXT_LABEL: Record<NextAction, string> = {
  assign: "等待指派",
  ack: "接單",
  permit_request: "技術員申請作業許可",
  permit_approve: "班長核准作業許可",
  loto: "依序勾選 LOTO",
  checklist: "完成維修 Checklist",
  report: "提交結案回報",
};

/** 下一步要操作的卡片：加上青色外框 */
export const nextRing = (on: boolean) => (on ? "ring-2 ring-cyan-400/80 shadow-[0_0_18px_rgba(34,211,238,0.25)]" : "");

// ---------------------------------------------------------------- 狀態列
export function StatusStepper({ wo }: { wo: WorkOrderDetail }) {
  const flow: WorkOrderStatus[] = isSafety(wo)
    ? ["ASSIGNED", "ACKED", "CLOSED"]
    : requirePermit(wo)
      ? ["ASSIGNED", "ACKED", "PERMIT_REQUESTED", "PERMIT_APPROVED", "LOTO_DONE", "IN_REPAIR", "PENDING_VERIFY", "CLOSED"]
      : ["ASSIGNED", "ACKED", "LOTO_DONE", "IN_REPAIR", "PENDING_VERIFY", "CLOSED"];
  const na = nextAction(wo);
  if (wo.status === "CANCELLED")
    return <div className="rounded border border-slate-500/50 bg-slate-500/10 px-3 py-1.5 text-sm text-slate-300">此工單已由主管標記為誤報並取消</div>;
  return (
    <ol className="flex flex-wrap items-center gap-1 text-xs">
      {flow.map((s, i) => {
        const done = reached(wo.status, s) && wo.status !== s;
        const cur = wo.status === s || (s === "ASSIGNED" && wo.status === "ESCALATED");
        return (
          <li key={s} className="flex items-center gap-1">
            <span
              className={cn(
                "flex items-center gap-1 rounded-full border px-2 py-0.5",
                cur ? "border-cyan-400 bg-cyan-500/20 text-cyan-200" : done ? "border-emerald-600/60 text-emerald-300" : "border-line text-muted",
              )}
            >
              {done ? <CheckCircle2 className="h-3 w-3" /> : <Circle className="h-3 w-3" />}
              {STATUS_LABEL[s]}
            </span>
            {i < flow.length - 1 && <span className="text-muted">›</span>}
          </li>
        );
      })}
      {na && (
        <li className="ml-auto flex items-center gap-1 rounded-full bg-cyan-500/15 px-2.5 py-0.5 text-sm font-medium text-cyan-200">
          下一步：{NEXT_LABEL[na]}
        </li>
      )}
    </ol>
  );
}

// ---------------------------------------------------------------- 標頭與操作
export function WorkorderHeader({ wo, act, busy, compact }: { wo: WorkOrderDetail; act: Act; busy: boolean; compact?: boolean }) {
  const persona = usePersona();
  const ro = persona.role === "judge";
  const canAck = ["OPEN", "ASSIGNED", "ESCALATED"].includes(wo.status);
  const sev = (wo.severity ?? wo.incident?.final_severity) as Severity;
  return (
    <Card>
      <div className="flex flex-wrap items-start gap-3 p-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge s={sev} />
            <span className="font-mono text-sm text-muted">{wo.id}</span>
            <Badge>{CATEGORY_LABEL[wo.category ?? wo.incident.category]}</Badge>
            <Badge className="border-cyan-600/40 text-cyan-300">{STATUS_LABEL[wo.status]}</Badge>
            {wo.escalation_level > 0 && <Badge className="border-red-500/50 text-red-300">已升級 {wo.escalation_level} 次</Badge>}
          </div>
          <h1 className={cn("mt-1 font-semibold", compact ? "text-lg" : "text-xl")}>{wo.title ?? wo.incident.title ?? wo.incident.type}</h1>
          <div className="mt-0.5 text-xs text-muted">
            {LINE_LABEL[wo.line ?? wo.incident.line] ?? wo.line}・<span className="font-mono">{wo.machine_id ?? wo.incident.machine_id}</span>・規則{" "}
            <span className="font-mono">{wo.incident.rule_id}</span>・建立 {fmtTime(wo.created_at)}
          </div>
          <div className="mt-1.5 text-sm">
            負責人：<span className="font-semibold">{wo.assignee_name ?? wo.assignee_id ?? "未指派"}</span>{" "}
            <span className="text-muted">{PERSON_ROLE_LABEL[wo.assignee_role ?? ""] ?? wo.assignee_role}</span>
            {canAck && <Countdown deadline={wo.ack_deadline} className="ml-2" />}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canAck && (
            <Button variant="primary" size={compact ? "lg" : "md"} className={nextRing(nextAction(wo) === "ack")} disabled={ro || busy} onClick={() => act(() => api.ack(wo.id), "已接單")}>
              接單
            </Button>
          )}
          {!compact && canAck && (persona.role === "leader" || persona.role === "supervisor") && (
            <Button disabled={busy} onClick={() => act(() => api.reassign(wo.id, null, "班長手動轉派"), "已轉派")}>
              轉派
            </Button>
          )}
          {!compact && persona.role === "supervisor" && !["CLOSED", "CANCELLED"].includes(wo.status) && (
            <SupervisorOverride wo={wo} act={act} busy={busy} />
          )}
        </div>
      </div>
      <div className="border-t border-line px-3 py-2">
        <StatusStepper wo={wo} />
      </div>
    </Card>
  );
}

function SupervisorOverride({ wo, act, busy }: { wo: WorkOrderDetail; act: Act; busy: boolean }) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 px-2 py-1">
      <span className="text-xs text-amber-300">主管覆寫</span>
      <select
        className="h-7 rounded border border-line bg-panel-2 px-1 text-xs"
        value={wo.severity ?? wo.incident.final_severity}
        disabled={busy}
        onChange={(e) =>
          act(() => api.override({ target: "workorder", target_id: wo.id, field: "final_severity", new: e.target.value, reason: "主管調整嚴重度" }), "嚴重度已覆寫")
        }
      >
        {["S1", "S2", "S3"].map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => act(() => api.override({ target: "workorder", target_id: wo.id, field: "false_alarm", new: true, reason: "現場確認為誤報" }), "已標記誤報")}>
        標記誤報
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------- 左：事件資訊
const SIGNALS_BY_TYPE: Record<string, string[]> = {
  servo_roll_feeder: ["feed_rate", "feed_length_error_mm", "motor_current_a"],
  servo_press: ["spm", "motion_score", "tonnage_peak"],
};

function guessSource(type: string) {
  if (type === "feed_jam") return "feed_jam";
  if (type === "ppe_violation") return "no_helmet";
  if (type.includes("smoke")) return "smoke";
  if (type.includes("mismatch")) return "idle";
  return "normal";
}

export function EventInfo({ wo }: { wo: WorkOrderDetail }) {
  const inc = wo.incident;
  const mid = inc.machine_id ?? "";
  const signals = useAsync(() => (mid ? api.signals(mid, 5) : Promise.resolve({ machine_id: "", points: [] })), [mid]);
  const { reload } = signals;
  useEffect(() => {
    if (!mid) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [mid, reload]);
  const points = useMemo(() => signals.data?.points ?? [], [signals.data]);
  const keys = useMemo(() => {
    const first = points[0];
    if (!first) return [];
    const t = Object.keys(SIGNALS_BY_TYPE).find((k) => (k === "servo_roll_feeder" ? "feed_rate" in first : k === "servo_press" ? "spm" in first : false));
    return (t ? SIGNALS_BY_TYPE[t] : Object.keys(first).filter((k) => k !== "ts" && k !== "state_code")).slice(0, 3);
  }, [points]);
  const marker = useMemo(() => {
    const t = new Date(inc.opened_at).getTime();
    let best: string | null = null;
    let bd = Infinity;
    for (const p of points) {
      const d = Math.abs(new Date(p.ts).getTime() - t);
      if (d < bd) {
        bd = d;
        best = p.ts;
      }
    }
    return bd < 5000 ? best : null;
  }, [points, inc.opened_at]);
  const overlays: Overlay[] = inc.jev_box ? [{ box: inc.jev_box, label: "Jev Box", kind: "jev" }] : [];
  const camera = { id: inc.camera_id ?? "CAM", source: guessSource(inc.type) };

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader title="關鍵幀" icon={<AlertOctagon className="h-4 w-4" />} right={<span className="font-mono text-xs text-muted">{inc.camera_id ?? "無攝影機"}・{fmtTime(inc.opened_at)}</span>} />
        <CardContent>
          {inc.camera_id ? (
            inc.keyframe_url ? (
              <div className="relative aspect-video overflow-hidden rounded bg-black">
                <img src={inc.keyframe_url} alt="關鍵幀" className="h-full w-full object-fill" />
                <OverlayInline overlays={overlays} />
              </div>
            ) : (
              <CameraView camera={camera} overlays={overlays} frozen className="rounded" />
            )
          ) : (
            <div className="rounded border border-dashed border-line p-4 text-center text-xs text-muted">此異常由 Log 規則觸發，沒有關鍵幀</div>
          )}
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            {Object.entries(inc.facts ?? {}).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2 border-b border-line/40 py-0.5">
                <dt className="truncate font-mono text-muted">{k}</dt>
                <dd className="tabular text-right font-mono">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
      {mid && (
        <Card>
          <CardHeader title="機台訊號（近 5 分鐘）" right={<span className="font-mono text-xs text-muted">{mid}</span>} />
          <CardContent>{points.length ? <SignalCharts points={points} signals={keys} marker={marker} /> : <div className="h-24 text-xs text-muted">載入中…</div>}</CardContent>
        </Card>
      )}
      <Card>
        <CardHeader title="狀態時間線" icon={<History className="h-4 w-4" />} />
        <CardContent>
          <ol className="relative space-y-2 border-l border-line pl-4">
            {(wo.transitions ?? []).map((t, i) => (
              <li key={i} className="relative text-xs">
                <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-cyan-400 bg-background" />
                <div className="flex items-center gap-2">
                  <span className="tabular font-mono text-muted">{fmtTime(t.ts)}</span>
                  <span className="font-medium">{STATUS_LABEL[t.to] ?? t.to}</span>
                  <span className="text-muted">by {t.actor}</span>
                </div>
                {t.reason && <div className="text-muted">{t.reason}</div>}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}

function OverlayInline({ overlays }: { overlays: Overlay[] }) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {overlays.map((o, i) => (
        <div
          key={i}
          className="absolute border-2 border-cyan-300"
          style={{ left: `${(o.box[0] / 1280) * 100}%`, top: `${(o.box[1] / 720) * 100}%`, width: `${((o.box[2] - o.box[0]) / 1280) * 100}%`, height: `${((o.box[3] - o.box[1]) / 720) * 100}%` }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 中：診斷
export function DiagnosisPanel({ wo }: { wo: WorkOrderDetail }) {
  const d = wo.diagnosis ?? wo.incident.diagnosis;
  const top = d?.root_cause_top3 ?? [];
  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title="根因前三名"
          icon={<Sparkles className="h-4 w-4" />}
          right={
            d && (
              <span className="text-xs text-muted">
                {d.mode === "jev_top3" ? "Jev 1.14" : d.mode === "llm_deep" ? "DeepSeek 深度診斷" : "規則"}・信心 {pct(d.confidence)}
                {d.p_false_alarm !== undefined && <>・誤報機率 {pct(d.p_false_alarm)}</>}
              </span>
            )
          }
        />
        <CardContent>
          {top.length ? (
            <RootCauseBars items={top} />
          ) : (
            <div className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200">AI 判斷暫不可用：規則照常建單派工，請依 SOP 排查。</div>
          )}
          {d?.questions_ver && <div className="mt-1 text-right font-mono text-[11px] text-muted">題目版本 {d.questions_ver}</div>}
          {!!d?.similar_cases?.length && (
            <div className="mt-2 border-t border-line/60 pt-2">
              <div className="mb-1 text-xs text-muted">判斷時引用的相似案例</div>
              <ul className="space-y-1">
                {d.similar_cases.map((c, i) => {
                  const sc = typeof c === "string" ? { case_id: c } : c;
                  return (
                    <li key={i} className={cn("flex flex-wrap items-baseline gap-x-2 rounded px-2 py-1 text-sm", sc.just_approved ? "border border-emerald-500/50 bg-emerald-500/10" : "bg-panel-2/50")}>
                      <span className="font-mono text-cyan-300">{sc.case_id}</span>
                      {sc.just_approved && <span className="text-xs font-semibold text-emerald-300">剛核准入庫</span>}
                      {(sc.title || sc.final_root_cause_name || sc.final_root_cause) && (
                        <span className="text-slate-300">{sc.title ?? `最後根因：${sc.final_root_cause_name ?? sc.final_root_cause}`}</span>
                      )}
                      {typeof sc.score === "number" && <span className="ml-auto font-mono text-xs text-muted">相似度 {sc.score.toFixed(2)}</span>}
                    </li>
                  );
                })}
              </ul>
              {d.similar_cases.some((c) => typeof c !== "string" && c.just_approved) && (
                <p className="mt-1 text-xs text-muted">新案例會被引用，但根因機率不一定因此上升；最後仍以現場排查結果為準。</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader title="AI 說明（DeepSeek）" icon={<Bot className="h-4 w-4" />} right={<ExplStatus s={d?.explanation_status} />} />
        <CardContent>
          {d?.explanation ? (
            <p className="text-sm leading-relaxed text-slate-200">
              {d.explanation}
              {d.explanation_status === "streaming" && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-cyan-400 align-middle" />}
            </p>
          ) : (
            <p className="text-sm text-muted">{isSafety(wo) ? "工安類異常直接派工，不呼叫 LLM（省時間也省成本）。" : d?.explanation_status === "failed" ? "說明產生中斷，請參考下方 SOP。" : "說明產生中…（派工通知已先送出）"}</p>
          )}
        </CardContent>
      </Card>
      <SopCard wo={wo} />
    </div>
  );
}

/** 每段 SOP：階段標籤＋第一個動作句，可展開；Agent 尚不可用的階段顯示鎖頭 */
function firstSentence(steps: string[]) {
  const t = (steps[0] ?? "").replace(/^\s*(\d+[.、)]|[-•])\s*/, "").replace(/^SOP-[\w-]+《[^》]*》/, "");
  const cut = t.split(/[。；;]/)[0];
  return cut.length > 46 ? cut.slice(0, 46) + "…" : cut;
}

function SopCard({ wo }: { wo: WorkOrderDetail }) {
  const sops = wo.sops ?? [];
  const groups: { id: string; title: string; items: typeof sops }[] = [];
  for (const s of sops) {
    const g = groups.find((x) => x.id === s.sop_id);
    if (g) g.items.push(s);
    else groups.push({ id: s.sop_id, title: s.title, items: [s] });
  }
  const note = (wo as WorkOrderDetail & { sop_note?: string | null }).sop_note;
  return (
    <Card>
      <CardHeader title="SOP" icon={<FileText className="h-4 w-4" />} right={<span className="text-xs text-muted">點階段展開</span>} />
      <CardContent className="space-y-3">
        {note && <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-200">{note}</div>}
        {groups.map((g) => (
          <div key={g.id}>
            <div className="mb-1.5 flex items-baseline gap-2">
              <span className="font-mono text-sm text-cyan-300">{g.id}</span>
              <span className="text-sm font-medium">{g.title}</span>
            </div>
            <div className="space-y-1">
              {g.items.map((s, i) => {
                const locked = !isSafety(wo) && ["inspect", "repair"].includes(s.phase) && !reached(wo.status, "IN_REPAIR");
                return (
                  <details key={i} className="group rounded border border-line bg-panel-2/50">
                    <summary className="flex cursor-pointer list-none items-center gap-2 px-2 py-1.5 text-sm">
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted transition-transform group-open:rotate-90" />
                      <Badge className={cn("shrink-0", locked ? "border-amber-500/50 text-amber-300" : "border-cyan-600/40 text-cyan-200")}>{PHASE[s.phase] ?? s.phase}</Badge>
                      <span className="min-w-0 flex-1 truncate text-slate-200">{firstSentence(s.steps)}</span>
                      {locked && <Lock className="h-3.5 w-3.5 shrink-0 text-amber-400" aria-label="作業許可核准前 Agent 不可使用此階段" />}
                    </summary>
                    <ul className="space-y-1 px-3 pb-2 text-sm leading-relaxed text-slate-300">
                      {s.steps.map((st, j) => (
                        <li key={j}>{st}</li>
                      ))}
                    </ul>
                    {s.source && <div className="px-3 pb-1.5 text-xs text-muted">來源：{s.source}</div>}
                  </details>
                );
              })}
            </div>
          </div>
        ))}
        {!sops.length && <div className="text-sm text-muted">沒有對應的 SOP</div>}
      </CardContent>
    </Card>
  );
}

const PHASE: Record<string, string> = {
  summary: "摘要",
  prepare: "準備",
  isolate: "隔離／LOTO",
  inspect: "排查",
  repair: "維修",
  verify: "驗證",
  restore: "復機",
  respond: "處置",
};

function ExplStatus({ s }: { s?: string }) {
  if (s === "streaming") return <span className="text-xs text-cyan-300">產生中…</span>;
  if (s === "done") return <span className="text-xs text-emerald-400">已補上</span>;
  if (s === "failed") return <span className="text-xs text-amber-300">中斷</span>;
  if (s === "pending") return <span className="text-xs text-muted">排隊中</span>;
  return null;
}

// ---------------------------------------------------------------- 右：LOTO → 許可 → Agent
function Locked({ locked, reason, children }: { locked: boolean; reason: string; children: React.ReactNode }) {
  return (
    <div className="relative">
      <div className={cn(locked && "pointer-events-none select-none opacity-30 blur-[1px]")}>{children}</div>
      {locked && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="flex items-center gap-1.5 rounded-md border border-amber-500/50 bg-[#1a1408]/90 px-3 py-1.5 text-xs text-amber-200">
            <Lock className="h-3.5 w-3.5" /> {reason}
          </span>
        </div>
      )}
    </div>
  );
}

export function LotoPanel({ wo, act, busy, big }: { wo: WorkOrderDetail; act: Act; busy: boolean; big?: boolean }) {
  const ro = usePersona().role === "judge";
  if (!wo.loto) return null;
  const steps = wo.loto.steps;
  const doneN = steps.filter((s) => s.checked_by).length;
  // 新流程：作業許可核准（PERMIT_APPROVED）後才能勾；不需許可的舊流程：接單後即可
  const unlockAt: WorkOrderStatus = requirePermit(wo) ? "PERMIT_APPROVED" : "ACKED";
  const editable = wo.status === unlockAt || wo.status === "LOTO_DONE";
  const nextNo = steps.find((s) => !s.checked_by)?.step_no;
  return (
    <Card className={nextRing(nextAction(wo) === "loto")}>
      <CardHeader
        title={`LOTO 上鎖掛牌・${wo.loto.loto_id}`}
        icon={<KeyRound className="h-4 w-4" />}
        right={<span className={cn("tabular text-xs", doneN === steps.length ? "text-emerald-400" : "text-amber-300")}>{doneN}/{steps.length}</span>}
      />
      <CardContent>
        <Locked locked={!reached(wo.status, unlockAt)} reason={requirePermit(wo) ? "作業許可核准後解鎖" : "接單後解鎖"}>
          <ul className="space-y-1.5">
            {steps.map((s) => {
              const can = editable && !ro && s.step_no === nextNo && !busy;
              return (
                <li key={s.step_no}>
                  <button
                    disabled={!can}
                    onClick={() => act(() => api.checkLoto(wo.id, s.step_no))}
                    className={cn(
                      "flex w-full items-start gap-2 rounded border px-2 text-left transition-colors",
                      big ? "py-3 text-base" : "py-1.5 text-sm",
                      s.checked_by ? "border-emerald-700/50 bg-emerald-500/10" : can ? "border-cyan-500/60 bg-cyan-500/5 hover:bg-cyan-500/10" : "border-line",
                    )}
                  >
                    {s.checked_by ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" /> : <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted" />}
                    <span className="flex-1">
                      {s.energy && <span className="mr-1.5 rounded bg-panel-2 px-1 text-[11px] text-muted">{s.energy}</span>}
                      {s.text}
                    </span>
                    {s.ts && <span className="tabular shrink-0 font-mono text-[11px] text-muted">{fmtTime(s.ts)}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-xs text-muted">LOTO 勾完並驗證零能量後才進入維修；在此之前維修 Agent 只提供上鎖與準備步驟（程式強制，不靠提示詞）。</p>
        </Locked>
      </CardContent>
    </Card>
  );
}

export function PermitPanel({ wo, act, busy }: { wo: WorkOrderDetail; act: Act; busy: boolean }) {
  const persona = usePersona();
  if (isSafety(wo) || !requirePermit(wo)) return null;
  const p = wo.permit ?? null;
  const st = p?.status ?? (p?.approved_by ? "approved" : wo.status === "PERMIT_REQUESTED" ? "requested" : p ? "rejected" : null);
  const na = nextAction(wo);
  const canApprove = persona.role === "leader" || persona.role === "supervisor";
  const tag =
    st === "approved" ? (
      <span className="text-xs text-emerald-400">已核准</span>
    ) : st === "requested" ? (
      <span className="text-xs text-amber-300">待班長核准</span>
    ) : st === "rejected" ? (
      <span className="text-xs text-red-300">已退回</span>
    ) : null;
  return (
    <Card className={nextRing(na === "permit_request" || na === "permit_approve")}>
      <CardHeader title="作業許可（設施規則第57條）" icon={<ShieldCheck className="h-4 w-4" />} right={tag} />
      <CardContent>
        <Locked locked={!reached(wo.status, "ACKED")} reason="接單後解鎖">
          <div className="space-y-2 text-sm">
            <div className="flex justify-between text-sm">
              <span className="text-muted">申請人</span>
              <span>{p?.requested_by ? `${p.requested_by}・${fmtTime(p.requested_at ?? p.ts)}` : "—"}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted">核准人（班長以上）</span>
              <span>{p?.approved_by ? `${p.approved_by}・${fmtTime(p.ts)}` : "—"}</span>
            </div>
            {st === "rejected" && wo.status === "ACKED" && (
              <div className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs text-red-200">上次申請被退回，請補齊危害防止措施後重新申請。</div>
            )}
            {wo.status === "ACKED" && (
              <Button
                className={cn("w-full", nextRing(na === "permit_request"))}
                variant="primary"
                disabled={busy || persona.role === "judge"}
                onClick={() => act(() => api.permit(wo.id, "request"), "已送出作業許可申請")}
              >
                申請作業許可
              </Button>
            )}
            {wo.status === "PERMIT_REQUESTED" && (
              <div className="flex gap-2">
                <Button
                  className={cn("flex-1", nextRing(na === "permit_approve" && canApprove))}
                  variant="ok"
                  disabled={busy || !canApprove}
                  onClick={() => act(() => api.permit(wo.id, "approve"), "作業許可已核准")}
                  title={canApprove ? undefined : "班長以上才能核准（右上角切換身分）"}
                >
                  {canApprove ? "核准作業許可" : "等待班長核准（切換為班長）"}
                </Button>
                {canApprove && (
                  <Button variant="danger" disabled={busy} onClick={() => act(() => api.permit(wo.id, "reject"), "已退回作業許可")}>
                    退回
                  </Button>
                )}
              </div>
            )}
          </div>
        </Locked>
      </CardContent>
    </Card>
  );
}

export function AgentChat({ wo, act, busy, streaming, tall }: { wo: WorkOrderDetail; act: Act; busy: boolean; streaming: Streaming | null; tall?: boolean }) {
  const persona = usePersona();
  const [text, setText] = useState("");
  const [thinking, setThinking] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const msgs = useMemo(() => sortMessages(wo.agent_messages ?? []), [wo.agent_messages]);
  // 新訊息時把對話框捲到底（只捲對話框，不捲整頁）
  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs.length, streaming?.text, thinking]);
  const open = reached(wo.status, "ACKED") && wo.status !== "CLOSED";
  const full = isSafety(wo) || reached(wo.status, "IN_REPAIR");
  const send = async (t: string) => {
    if (!t.trim()) return;
    setText("");
    setThinking(true);
    try {
      await act(() => api.sendAgent(wo.id, t));
    } finally {
      setThinking(false);
    }
  };
  const quick = full
    ? ["開始排查", "滾輪看起來正常，還是卡", "板厚量到 2.08 mm", "已試沖 10 模正常"]
    : ["我要準備什麼？", "LOTO 要注意什麼？"];
  return (
    <Card className="flex flex-col">
      <CardHeader
        title="維修 Agent"
        icon={<Wrench className="h-4 w-4" />}
        right={
          open ? (
            full ? (
              <span className="text-xs text-emerald-400">排查／維修模式</span>
            ) : (
              <span className="text-xs text-amber-300">受限模式：只提供 LOTO 與準備</span>
            )
          ) : null
        }
      />
      <Locked locked={!open && wo.status !== "CLOSED"} reason="接單後開放對話">
        <div ref={boxRef} className={cn("space-y-2 overflow-y-auto p-3", tall ? "max-h-[65vh] min-h-[260px]" : "max-h-[560px] min-h-[180px]")}>
          {msgs.map((m) => (
            <ChatBubble key={m.id} m={m} wo={wo} act={act} />
          ))}
          {streaming && !msgs.some((x) => String(x.id) === String(streaming.message_id)) && (
            <div className="max-w-[96%] rounded-lg rounded-tl-none border border-line bg-panel-2 px-3 py-2">
              <AssistantContent
                m={{
                  id: streaming.message_id,
                  workorder_id: wo.id,
                  role: "assistant",
                  content: streaming.text,
                  safety_flags: streaming.flags ?? [],
                  gate: streaming.gate ?? { status: "pending", checked: [] },
                  ts: "",
                  streaming: true,
                }}
              />
              <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-cyan-400 align-middle" />
            </div>
          )}
          {thinking && !streaming && msgs.at(-1)?.role !== "assistant" && (
            <div className="flex w-fit items-center gap-2 rounded-lg rounded-tl-none border border-line bg-panel-2 px-3 py-2 text-sm text-muted">
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-cyan-400" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-cyan-400 [animation-delay:150ms]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-cyan-400 [animation-delay:300ms]" />
              </span>
              Agent 整理工單、SOP 與訊號中…
            </div>
          )}
          {!msgs.length && !streaming && !thinking && <div className="py-6 text-center text-xs text-muted">向 Agent 描述現場狀況，它會依根因排序與 SOP 帶你排查</div>}
        </div>
        <div className="border-t border-line p-2">
          <div className="mb-2 flex flex-wrap gap-1">
            {quick.map((q) => (
              <button key={q} disabled={!open || busy || persona.role === "judge"} onClick={() => send(q)} className="rounded-full border border-line px-2 py-0.5 text-xs text-muted hover:border-cyan-500 hover:text-foreground disabled:opacity-40">
                {q}
              </button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void send(text);
            }}
          >
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={!open || persona.role === "judge"}
              placeholder={persona.role === "judge" ? "評審唯讀" : "輸入現場狀況…"}
              className="h-10 min-w-0 flex-1 rounded-md border border-line bg-panel-2 px-3 text-sm focus:border-cyan-500 focus:outline-none"
            />
            <Button type="submit" variant="primary" className="h-10" disabled={!open || busy || !text.trim() || persona.role === "judge"} aria-label="送出">
              <Send className="h-4 w-4" />
            </Button>
          </form>
        </div>
      </Locked>
    </Card>
  );
}

export function ChecklistPanel({ wo, act, busy, big }: { wo: WorkOrderDetail; act: Act; busy: boolean; big?: boolean }) {
  const ro = usePersona().role === "judge";
  const items = wo.checklist ?? [];
  const unlockAt: WorkOrderStatus = isSafety(wo) ? "ACKED" : "IN_REPAIR";
  const editable = isSafety(wo) ? wo.status === "ACKED" : wo.status === "IN_REPAIR";
  return (
    <Card className={nextRing(nextAction(wo) === "checklist")}>
      <CardHeader title="完工 Checklist" icon={<ClipboardCheck className="h-4 w-4" />} right={<span className="tabular text-xs text-muted">{items.filter((i) => i.checked_by).length}/{items.length}</span>} />
      <CardContent>
        <Locked locked={!reached(wo.status, unlockAt)} reason={isSafety(wo) ? "接單後解鎖" : "作業許可核准後解鎖"}>
          <ul className="space-y-1.5">
            {items.map((it) => (
              <li key={it.item}>
                <button
                  disabled={!editable || !!it.checked_by || busy || ro}
                  onClick={() => act(() => api.checkItem(wo.id, it.item))}
                  className={cn(
                    "flex w-full items-center gap-2 rounded border px-2 text-left",
                    big ? "py-3 text-base" : "py-1.5 text-sm",
                    it.checked_by ? "border-emerald-700/50 bg-emerald-500/10" : editable ? "border-cyan-500/40 hover:bg-cyan-500/10" : "border-line",
                  )}
                >
                  {it.checked_by ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> : <Circle className="h-4 w-4 shrink-0 text-muted" />}
                  <span className="flex-1">{it.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </Locked>
      </CardContent>
    </Card>
  );
}

export function ReportPanel({ wo, act, busy }: { wo: WorkOrderDetail; act: Act; busy: boolean }) {
  const ro = usePersona().role === "judge";
  const top = (wo.diagnosis ?? wo.incident.diagnosis)?.root_cause_top3 ?? [];
  const [text, setText] = useState("");
  const [rc, setRc] = useState<string>("");
  const safety = isSafety(wo);
  const can = safety ? wo.status === "ACKED" : wo.status === "PENDING_VERIFY";
  const check = wo.report?.check;
  const fill = () => {
    if (wo.incident.type === "feed_jam") {
      // 只能選下拉選單裡有的選項（前三名＋OTHER），不然 select 會顯示空白但送出看不見的值
      const thick = top.find((r) => /thick|厚度/i.test(`${r.failure_mode_id}${r.name}`));
      setRc(thick?.failure_mode_id ?? "OTHER");
      setText("實際原因：材料板厚 2.08 mm 超出公差（滾輪間隙檢查正常，不是滾輪問題）。處置：隔離該批材料並換料，重設送料長度。結果：試沖 10 模送料誤差 0.02 mm，已恢復正常。");
    } else if (safety) setText("班長到場提醒人員配戴安全帽，已完成現場宣導並記錄。");
    else {
      setRc(top[0]?.failure_mode_id ?? "");
      setText(`實際原因：${top[0]?.name ?? "依 SOP 排查確認"}。處置：依 SOP 完成更換與調整。結果：復機後運轉 10 分鐘確認正常。`);
    }
  };
  return (
    <Card className={nextRing(nextAction(wo) === "report")}>
      <CardHeader title="結案回報" icon={<FileText className="h-4 w-4" />} right={check && <span className={cn("text-xs", check.complete ? "text-emerald-400" : "text-red-300")}>{check.complete ? "J5 檢查通過" : "J5 退回"}</span>} />
      <CardContent>
        <Locked locked={!can && !wo.report} reason={safety ? "接單後可回報" : "Checklist 完成後解鎖"}>
          {wo.status === "CLOSED" && wo.report ? (
            <div className="space-y-1 text-sm">
              <p className="text-slate-200">{wo.report.text}</p>
              {wo.report.root_cause_confirmed && <div className="text-xs text-muted">確認根因：{wo.report.root_cause_confirmed}</div>}
              <WritebackStatus wo={wo} act={act} busy={busy} fallback={check?.kb_status} />
            </div>
          ) : (
            <div className="space-y-2">
              {!safety && (
                <select value={rc} onChange={(e) => setRc(e.target.value)} disabled={!can || ro} className="h-9 w-full rounded-md border border-line bg-panel-2 px-2 text-sm">
                  <option value="">確認的根因…</option>
                  {top.map((r) => (
                    <option key={r.failure_mode_id} value={r.failure_mode_id}>
                      {r.name}（{pct(r.p)}）
                    </option>
                  ))}
                  <option value="OTHER">其他（不在前三名）</option>
                </select>
              )}
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                disabled={!can || ro}
                rows={4}
                placeholder="處置步驟與驗證結果…"
                className="w-full rounded-md border border-line bg-panel-2 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none"
              />
              {check && !check.complete && <div className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs text-red-200">Jev 完整性檢查：缺少 {check.missing.join("、")}</div>}
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" disabled={!can || ro} onClick={fill}>
                  帶入範例
                </Button>
                <Button className="ml-auto" variant="primary" disabled={!can || busy || ro || !text.trim()} onClick={async () => {
                    const r = await act(() => api.report(wo.id, text, rc || null));
                    if (r?.check && !r.check.complete) toast(`J5 退回：回報缺少${r.check.missing.join("、")}，請補寫後再送`, "error");
                    else if (r) toast(r.check?.kb_status === "draft" ? "回報通過，工單已結案；案例草稿待主管核准入庫" : "回報通過，工單已結案", "ok");
                  }}
                >
                  提交回報（J5 檢查）
                </Button>
              </div>
            </div>
          )}
        </Locked>
      </CardContent>
    </Card>
  );
}

/** 知識回寫：結案時產生案例草稿，主管核准後才入庫（CL-3） */
function WritebackStatus({ wo, act, busy, fallback }: { wo: WorkOrderDetail; act: Act; busy: boolean; fallback?: string }) {
  const persona = usePersona();
  const wb = wo.writeback;
  if (!wb) return <div className="text-xs text-emerald-400">已寫回知識庫（{fallback ?? "draft"}，待審核）</div>;
  const approved = wb.status === "approved";
  const isSup = persona.role === "supervisor";
  return (
    <div className={cn("mt-2 flex flex-wrap items-center gap-2 rounded border px-2 py-1.5", approved ? "border-emerald-600/50 bg-emerald-500/10" : "border-amber-500/50 bg-amber-500/10")}>
      <span className="text-sm">
        知識回寫：<span className={approved ? "font-semibold text-emerald-300" : "font-semibold text-amber-200"}>{approved ? "已核准入庫" : wb.status === "missing" ? "草稿遺失" : "草稿（待主管審核）"}</span>
      </span>
      <span className="font-mono text-xs text-muted">{wb.case_id}</span>
      {wb.reviewed_by && <span className="text-xs text-muted">審核人 {wb.reviewed_by}</span>}
      {!approved && wb.status !== "missing" && (
        <Button
          size="sm"
          variant="ok"
          className="ml-auto"
          disabled={busy || !isSup}
          title={isSup ? undefined : "組長（主管）以上才能核准入庫"}
          onClick={async () => {
            const r = await act(() => api.approveCase(wb.case_id, "現場主管核准"));
            if (r) toast(`已核准入庫：寫入 ${r.ingested_chunks} 段知識`, "ok");
          }}
        >
          {isSup ? "核准入庫" : "核准入庫（需主管）"}
        </Button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 稽核時間線
export function AuditTimeline({ traceId, refreshKey, compact }: { traceId: string | null | undefined; refreshKey?: unknown; compact?: boolean }) {
  const q = useAsync(() => (traceId ? api.audit({ trace_id: traceId }) : Promise.resolve([] as AuditEntry[])), [traceId, refreshKey]);
  const list = q.data ?? [];
  return (
    <Card>
      <CardHeader title="稽核時間線" icon={<History className="h-4 w-4" />} right={<span className="font-mono text-xs text-muted">trace_id {traceId}</span>} />
      <CardContent>
        <AuditList list={list} compact={compact} />
      </CardContent>
    </Card>
  );
}

export { AuditList };
