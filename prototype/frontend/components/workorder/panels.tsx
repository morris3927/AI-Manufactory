"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertOctagon,
  Bot,
  CheckCircle2,
  Circle,
  ClipboardCheck,
  FileText,
  History,
  KeyRound,
  Lock,
  Send,
  ShieldAlert,
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
import type { AgentMessage, AuditEntry, Severity, WorkOrderDetail, WorkOrderStatus } from "@/lib/types";
import { cn, fmtDateTime, fmtTime, pct } from "@/lib/utils";
import type { Streaming } from "./use-workorder";

type Act = <T>(fn: () => Promise<T>, okText?: string) => Promise<T | undefined>;

const isSafety = (wo: WorkOrderDetail) => wo.category === "safety" || wo.incident?.category === "safety";

// ---------------------------------------------------------------- 狀態列
export function StatusStepper({ wo }: { wo: WorkOrderDetail }) {
  const flow: WorkOrderStatus[] = isSafety(wo) ? ["ASSIGNED", "ACKED", "CLOSED"] : ["ASSIGNED", "ACKED", "LOTO_DONE", "IN_REPAIR", "PENDING_VERIFY", "CLOSED"];
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
            <Button variant="primary" size={compact ? "lg" : "md"} disabled={ro || busy} onClick={() => act(() => api.ack(wo.id), "已接單")}>
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
      <span className="text-[11px] text-amber-300">主管覆寫</span>
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
        <CardHeader title="關鍵幀" icon={<AlertOctagon className="h-4 w-4" />} right={<span className="font-mono text-[11px] text-muted">{inc.camera_id ?? "無攝影機"}・{fmtTime(inc.opened_at)}</span>} />
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
          <CardHeader title="機台訊號（近 5 分鐘）" right={<span className="font-mono text-[11px] text-muted">{mid}</span>} />
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
              <span className="text-[11px] text-muted">
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
          {d?.questions_ver && <div className="mt-1 text-right font-mono text-[10px] text-muted">題目版本 {d.questions_ver}</div>}
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
      <Card>
        <CardHeader title="SOP" icon={<FileText className="h-4 w-4" />} />
        <CardContent className="space-y-2">
          {(wo.sops ?? []).map((s, i) => {
            const locked = !isSafety(wo) && ["inspect", "repair"].includes(s.phase) && !reached(wo.status, "IN_REPAIR");
            return (
              <details key={i} className="rounded border border-line bg-panel-2/50" open={i === 0}>
                <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-sm">
                  <span className="font-mono text-xs text-cyan-300">{s.sop_id}</span>
                  <span>{s.title}</span>
                  <Badge className="ml-auto">{PHASE[s.phase] ?? s.phase}</Badge>
                  {locked && <Lock className="h-3.5 w-3.5 text-amber-400" aria-label="Agent 在許可核准前不可使用" />}
                </summary>
                <ol className="list-decimal space-y-0.5 px-7 pb-2 text-xs text-slate-300">
                  {s.steps.map((st, j) => (
                    <li key={j}>{st}</li>
                  ))}
                </ol>
              </details>
            );
          })}
          {!wo.sops?.length && <div className="text-xs text-muted">沒有對應的 SOP</div>}
        </CardContent>
      </Card>
    </div>
  );
}

const PHASE: Record<string, string> = { prepare: "準備", inspect: "排查", repair: "維修", verify: "驗證", respond: "處置" };

function ExplStatus({ s }: { s?: string }) {
  if (s === "streaming") return <span className="text-[11px] text-cyan-300">產生中…</span>;
  if (s === "done") return <span className="text-[11px] text-emerald-400">已補上</span>;
  if (s === "failed") return <span className="text-[11px] text-amber-300">中斷</span>;
  if (s === "pending") return <span className="text-[11px] text-muted">排隊中</span>;
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
  const editable = wo.status === "ACKED";
  const nextNo = steps.find((s) => !s.checked_by)?.step_no;
  return (
    <Card>
      <CardHeader
        title={`LOTO 上鎖掛牌・${wo.loto.loto_id}`}
        icon={<KeyRound className="h-4 w-4" />}
        right={<span className={cn("tabular text-xs", doneN === steps.length ? "text-emerald-400" : "text-amber-300")}>{doneN}/{steps.length}</span>}
      />
      <CardContent>
        <Locked locked={!reached(wo.status, "ACKED")} reason="接單後解鎖">
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
                      <span className="mr-1.5 rounded bg-panel-2 px-1 text-[10px] text-muted">{s.energy}</span>
                      {s.text}
                    </span>
                    {s.ts && <span className="tabular shrink-0 font-mono text-[10px] text-muted">{fmtTime(s.ts)}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-[11px] text-muted">LOTO 未完成前，維修 Agent 只能提供上鎖與準備步驟（程式強制，不靠提示詞）。</p>
        </Locked>
      </CardContent>
    </Card>
  );
}

export function PermitPanel({ wo, act, busy }: { wo: WorkOrderDetail; act: Act; busy: boolean }) {
  const persona = usePersona();
  if (!wo.permit) return null;
  const p = wo.permit;
  const at = wo.status === "LOTO_DONE";
  return (
    <Card>
      <CardHeader title="作業許可" icon={<ShieldCheck className="h-4 w-4" />} right={p.approved_by ? <span className="text-xs text-emerald-400">已核准</span> : p.requested_by ? <span className="text-xs text-amber-300">待班長核准</span> : null} />
      <CardContent>
        <Locked locked={!reached(wo.status, "LOTO_DONE")} reason="LOTO 完成後解鎖">
          <div className="space-y-2 text-sm">
            <div className="flex justify-between text-xs">
              <span className="text-muted">申請人</span>
              <span>{p.requested_by ?? "—"}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted">核准人（班長）</span>
              <span>{p.approved_by ? `${p.approved_by}・${fmtTime(p.ts)}` : "—"}</span>
            </div>
            {at && !p.requested_by && (
              <Button className="w-full" variant="primary" disabled={busy || persona.role === "judge"} onClick={() => act(() => api.permit(wo.id, "request"), "已送出作業許可申請")}>
                申請作業許可
              </Button>
            )}
            {at && p.requested_by && !p.approved_by && (
              <Button
                className="w-full"
                variant="ok"
                disabled={busy || persona.role !== "leader"}
                onClick={() => act(() => api.permit(wo.id, "approve"), "作業許可已核准")}
                title={persona.role !== "leader" ? "只有班長能核准（右上角切換身分）" : undefined}
              >
                {persona.role === "leader" ? "班長核准作業許可" : "等待班長核准（切換為班長）"}
              </Button>
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
  const endRef = useRef<HTMLDivElement>(null);
  const msgs = wo.agent_messages ?? [];
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [msgs.length, streaming?.text]);
  const open = reached(wo.status, "ACKED") && wo.status !== "CLOSED";
  const full = isSafety(wo) || reached(wo.status, "IN_REPAIR");
  const send = async (t: string) => {
    if (!t.trim()) return;
    setText("");
    await act(() => api.sendAgent(wo.id, t));
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
              <span className="text-[11px] text-emerald-400">排查／維修模式</span>
            ) : (
              <span className="text-[11px] text-amber-300">受限模式：只提供 LOTO 與準備</span>
            )
          ) : null
        }
      />
      <Locked locked={!open && wo.status !== "CLOSED"} reason="接單後開放對話">
        <div className={cn("space-y-2 overflow-y-auto p-3", tall ? "max-h-[60vh] min-h-[240px]" : "max-h-[420px] min-h-[160px]")}>
          {msgs.map((m) => (
            <ChatBubble key={m.id} m={m} wo={wo} act={act} />
          ))}
          {streaming && (
            <div className="max-w-[92%] whitespace-pre-wrap rounded-lg rounded-tl-none border border-line bg-panel-2 px-3 py-2 text-sm text-slate-200">
              {streaming.text}
              <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-cyan-400 align-middle" />
            </div>
          )}
          {!msgs.length && !streaming && <div className="py-6 text-center text-xs text-muted">向 Agent 描述現場狀況，它會依根因排序與 SOP 帶你排查</div>}
          <div ref={endRef} />
        </div>
        <div className="border-t border-line p-2">
          <div className="mb-2 flex flex-wrap gap-1">
            {quick.map((q) => (
              <button key={q} disabled={!open || busy || persona.role === "judge"} onClick={() => send(q)} className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted hover:border-cyan-500 hover:text-foreground disabled:opacity-40">
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

function ChatBubble({ m, wo, act }: { m: AgentMessage; wo: WorkOrderDetail; act: Act }) {
  const persona = usePersona();
  if (m.role === "user")
    return (
      <div className="ml-auto max-w-[85%] rounded-lg rounded-tr-none bg-cyan-600/25 px-3 py-2 text-sm text-cyan-50">
        {m.content}
        <div className="mt-0.5 text-right font-mono text-[10px] text-cyan-200/50">{fmtTime(m.ts)}</div>
      </div>
    );
  if (m.role === "system")
    return (
      <div className={cn("rounded-md border px-3 py-1.5 text-xs", m.content.startsWith("安全把關") ? "border-red-500/60 bg-red-500/10 text-red-200" : "border-line bg-panel-2/60 text-muted")}>
        {m.content.startsWith("安全把關") && <ShieldAlert className="mr-1 inline h-3.5 w-3.5" />}
        {m.content}
      </div>
    );
  const flags = m.safety_flags ?? [];
  const pending = flags.some((f) => f.supervisor_requested);
  return (
    <div className="max-w-[94%] rounded-lg rounded-tl-none border border-line bg-panel-2 px-3 py-2 text-sm">
      <div className="text-slate-200">{m.content}</div>
      {!!m.steps?.length && (
        <ol className="mt-1.5 space-y-1">
          {m.steps.map((s, i) => {
            const f = flags.find((x) => x.step_index === i);
            return (
              <li key={i} className={cn("flex gap-2 rounded px-1.5 py-1", f ? "border border-red-500/70 bg-red-500/15" : "")}>
                <span className={cn("font-mono text-xs", f ? "text-red-300" : "text-cyan-300")}>{i + 1}.</span>
                <div className="flex-1">
                  <span className={cn(f && "font-semibold text-red-200 line-through decoration-red-400/70")}>{s}</span>
                  {f && (
                    <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-red-300">
                      <ShieldAlert className="h-3.5 w-3.5" /> Jev 安全把關：
                      {Object.entries(f.probs).map(([k, v]) => (
                        <span key={k} className="rounded bg-red-500/20 px-1 font-mono">
                          {k} {v.toFixed(2)}
                        </span>
                      ))}
                      {f.supervisor_requested ? <span>・已要求主管確認</span> : <span className="text-emerald-300">・主管已確認</span>}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {pending && persona.role === "supervisor" && (
        <Button
          size="sm"
          variant="warn"
          className="mt-2"
          onClick={() => act(() => api.override({ target: "workorder", target_id: wo.id, field: "safety_ack", new: m.id, reason: "禁止徒手進入模具區，改用取料夾具由送料口側取出" }), "已確認安全把關")}
        >
          主管確認：改用取料夾具
        </Button>
      )}
      {!!m.tool_calls?.length && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {m.tool_calls.map((t, i) => (
            <span key={i} className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[10px] text-muted" title={JSON.stringify(t.args ?? {})}>
              ƒ {t.name}
            </span>
          ))}
        </div>
      )}
      <div className="mt-0.5 font-mono text-[10px] text-muted">{fmtTime(m.ts)}</div>
    </div>
  );
}

export function ChecklistPanel({ wo, act, busy, big }: { wo: WorkOrderDetail; act: Act; busy: boolean; big?: boolean }) {
  const ro = usePersona().role === "judge";
  const items = wo.checklist ?? [];
  const unlockAt: WorkOrderStatus = isSafety(wo) ? "ACKED" : "IN_REPAIR";
  const editable = isSafety(wo) ? wo.status === "ACKED" : wo.status === "IN_REPAIR";
  return (
    <Card>
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
      setRc("FM-STP-MATERIAL-THICK");
      setText("滾輪間隙正常；量測板厚 2.08 mm 超出公差，隔離該批材料並換料，重設送料長度。試沖 10 模送料誤差 0.02 mm，確認恢復正常。");
    } else if (safety) setText("班長到場提醒人員配戴安全帽，已完成現場宣導並記錄。");
    else {
      setRc(top[0]?.failure_mode_id ?? "");
      setText("依 SOP 排查確認根因並完成處置，復機後運轉 10 分鐘確認正常。");
    }
  };
  return (
    <Card>
      <CardHeader title="結案回報" icon={<FileText className="h-4 w-4" />} right={check && <span className={cn("text-xs", check.complete ? "text-emerald-400" : "text-red-300")}>{check.complete ? "J5 檢查通過" : "J5 退回"}</span>} />
      <CardContent>
        <Locked locked={!can && !wo.report} reason={safety ? "接單後可回報" : "Checklist 完成後解鎖"}>
          {wo.status === "CLOSED" && wo.report ? (
            <div className="space-y-1 text-sm">
              <p className="text-slate-200">{wo.report.text}</p>
              {wo.report.root_cause_confirmed && <div className="text-xs text-muted">確認根因：{wo.report.root_cause_confirmed}</div>}
              <div className="text-xs text-emerald-400">已寫回知識庫（{check?.kb_status ?? "draft"}，待審核）</div>
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
                <Button className="ml-auto" variant="primary" disabled={!can || busy || ro || !text.trim()} onClick={() => act(() => api.report(wo.id, text, rc || null))}>
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

// ---------------------------------------------------------------- 稽核時間線
export function AuditTimeline({ traceId, refreshKey, compact }: { traceId: string | null | undefined; refreshKey?: unknown; compact?: boolean }) {
  const q = useAsync(() => (traceId ? api.audit({ trace_id: traceId }) : Promise.resolve([] as AuditEntry[])), [traceId, refreshKey]);
  const list = q.data ?? [];
  return (
    <Card>
      <CardHeader title="稽核時間線" icon={<History className="h-4 w-4" />} right={<span className="font-mono text-[11px] text-muted">trace_id {traceId}</span>} />
      <CardContent>
        <AuditList list={list} compact={compact} />
      </CardContent>
    </Card>
  );
}

const ACTOR_STYLE: Record<string, string> = {
  jev: "bg-orange-500/20 text-orange-200",
  llm: "bg-emerald-500/20 text-emerald-200",
  rules: "bg-blue-500/20 text-blue-200",
  system: "bg-slate-500/20 text-slate-200",
  agent: "bg-violet-500/20 text-violet-200",
};

export function AuditList({ list, compact }: { list: AuditEntry[]; compact?: boolean }) {
  if (!list.length) return <div className="py-4 text-center text-xs text-muted">沒有紀錄</div>;
  return (
    <ol className={cn("relative space-y-1.5 border-l border-line pl-4", compact && "text-xs")}>
      {list.map((a, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[20px] top-1.5 h-2 w-2 rounded-full bg-cyan-400" />
          <div className="flex flex-wrap items-baseline gap-2 text-xs">
            <span className="tabular font-mono text-muted">{fmtDateTime(a.ts)}</span>
            <span className={cn("rounded px-1.5 py-px font-mono text-[10px]", ACTOR_STYLE[a.actor] ?? "bg-cyan-500/15 text-cyan-200")}>{a.actor}</span>
            <span className="font-medium text-slate-200">{a.action}</span>
          </div>
          {Object.keys(a.detail ?? {}).length > 0 && <div className="mt-0.5 break-all font-mono text-[11px] text-muted">{JSON.stringify(a.detail)}</div>}
        </li>
      ))}
    </ol>
  );
}
