"use client";

// 職災 8 小時通報（職業安全衛生法第37條第2項）。
// 系統只建立草稿與倒數；一定要由組長（主管）以上確認後按「送出通報」，系統不會自動送出。

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, Siren } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync, useNow, useThrottled } from "@/lib/hooks";
import { usePersona } from "@/lib/session";
import type { Incident, IncidentReportStatus } from "@/lib/types";
import { cn, fmtDateTime, fmtDuration } from "@/lib/utils";
import { useWs } from "@/lib/ws";
import { toast } from "./toast";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader } from "./ui/card";

export const SCENE_NOTICE = "非經司法機關或檢查機構許可，不得移動或破壞現場";

function useRemaining(deadline: string | null | undefined, fallback?: number | null) {
  const now = useNow();
  if (deadline && now) return Math.floor((new Date(deadline).getTime() - now) / 1000);
  return fallback ?? null;
}

/** 儀表板頂端的紅色倒數條 */
export function ReportAlertBar({ inc, href }: { inc: Incident; href?: string }) {
  const r = inc.incident_report;
  const left = useRemaining(r?.deadline, r?.remaining_s);
  if (!r || (r.status !== "draft" && r.status !== "overdue")) return null;
  const overdue = r.status === "overdue" || (left !== null && left <= 0);
  const body = (
    <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border-2 px-4 py-2.5", overdue ? "border-red-400 bg-red-700/40" : "border-red-500 bg-red-600/20 pulse-red")}>
      <Siren className="h-6 w-6 shrink-0 text-red-300" />
      <div className="min-w-0">
        <div className="text-base font-bold text-red-100">
          職業災害 8 小時通報・{inc.title ?? inc.type}
          <span className="ml-2 text-sm font-normal text-red-200">{(r.criteria_met ?? []).join("、")}</span>
        </div>
        <div className="text-sm font-semibold text-amber-200">⚠ {SCENE_NOTICE}</div>
      </div>
      <div className="ml-auto text-right">
        <div className="text-xs text-red-200">{overdue ? "已逾期" : "距通報期限"}</div>
        <div className="tabular font-mono text-2xl font-bold text-white">{left !== null ? (overdue ? "00:00:00" : fmtDuration(left)) : "—"}</div>
      </div>
    </div>
  );
  return href ? (
    <Link href={href} className="block hover:brightness-110">
      {body}
    </Link>
  ) : (
    body
  );
}

const NUM_FIELDS: { k: "deaths" | "injured" | "hospitalized"; label: string }[] = [
  { k: "deaths", label: "死亡" },
  { k: "injured", label: "罹災" },
  { k: "hospitalized", label: "需住院" },
];

function CasualtyForm({ incidentId, onDone }: { incidentId: string; onDone: (r: IncidentReportStatus) => void }) {
  const persona = usePersona();
  const isSup = persona.role === "supervisor";
  const [v, setV] = useState({ deaths: 0, injured: 0, hospitalized: 0, designated: false, note: "" });
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">S3 事件：由主管確認現場傷亡後標記（系統不會從影像推論傷亡）。符合死亡、罹災 3 人以上、罹災需住院任一項，即建立 8 小時通報草稿。</p>
      <div className="flex flex-wrap items-end gap-3">
        {NUM_FIELDS.map((f) => (
          <label key={f.k} className="text-sm">
            <span className="block text-xs text-muted">{f.label}（人）</span>
            <input
              type="number"
              min={0}
              value={v[f.k]}
              onChange={(e) => setV((x) => ({ ...x, [f.k]: Math.max(0, Number(e.target.value) || 0) }))}
              className="h-9 w-20 rounded-md border border-line bg-panel-2 px-2"
              disabled={!isSup}
            />
          </label>
        ))}
        <label className="flex h-9 items-center gap-1.5 text-sm">
          <input type="checkbox" checked={v.designated} onChange={(e) => setV((x) => ({ ...x, designated: e.target.checked }))} disabled={!isSup} />
          其他經中央主管機關指定公告之災害
        </label>
      </div>
      <input
        value={v.note}
        onChange={(e) => setV((x) => ({ ...x, note: e.target.value }))}
        placeholder="備註（例如：右手壓傷送醫）"
        className="h-9 w-full rounded-md border border-line bg-panel-2 px-3 text-sm"
        disabled={!isSup}
      />
      <Button
        variant="danger"
        disabled={!isSup || busy}
        title={isSup ? undefined : "組長（主管）以上才能標記傷亡"}
        onClick={async () => {
          setBusy(true);
          try {
            const r = await api.casualty(incidentId, v);
            onDone(r);
            toast(r.required ? "已標記傷亡：符合第37條第2項，8 小時通報倒數開始" : "已標記傷亡：未達通報條件", r.required ? "error" : "ok");
          } catch (e) {
            toast((e as Error).message, "error");
          } finally {
            setBusy(false);
          }
        }}
      >
        {isSup ? "標記傷亡" : "標記傷亡（需主管身分）"}
      </Button>
    </div>
  );
}

function DraftEditor({ rep, onDone }: { rep: IncidentReportStatus; onDone: (r: IncidentReportStatus) => void }) {
  const persona = usePersona();
  const isSup = persona.role === "supervisor";
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const draft = rep.draft ?? {};
  const fields = Object.entries(draft).filter(([k]) => k !== "data_label");
  const show = (v: unknown) => (Array.isArray(v) ? v.join("、") : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? ""));
  return (
    <div className="space-y-2">
      <div className="grid gap-2 md:grid-cols-2">
        {fields.map(([k, v]) => {
          const long = show(v).length > 40;
          const val = edits[k] ?? show(v);
          return (
            <label key={k} className={cn("text-sm", long && "md:col-span-2")}>
              <span className="mb-0.5 block text-xs text-muted">{k}</span>
              {long ? (
                <textarea value={val} rows={3} onChange={(e) => setEdits((x) => ({ ...x, [k]: e.target.value }))} disabled={!isSup} className="w-full rounded-md border border-line bg-panel-2 px-2 py-1.5" />
              ) : (
                <input value={val} onChange={(e) => setEdits((x) => ({ ...x, [k]: e.target.value }))} disabled={!isSup} className="h-9 w-full rounded-md border border-line bg-panel-2 px-2" />
              )}
            </label>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">{String(draft.data_label ?? "模擬資料")}・系統不會自動送出，須由組長（主管）以上確認</span>
        <Button
          variant="danger"
          size="lg"
          className="ml-auto"
          disabled={!isSup || busy}
          title={isSup ? undefined : "組長（主管）以上才能送出通報"}
          onClick={async () => {
            if (!window.confirm("確認送出職業災害通報？")) return;
            setBusy(true);
            try {
              const r = await api.submitIncidentReport(rep.incident_id, Object.keys(edits).length ? edits : undefined);
              onDone(r);
              toast("通報已送出", "ok");
            } catch (e) {
              toast((e as Error).message, "error");
            } finally {
              setBusy(false);
            }
          }}
        >
          {isSup ? "送出通報" : "送出通報（需主管身分）"}
        </Button>
      </div>
    </div>
  );
}

/** 事件／工單詳情頁的職災通報區塊（S3 或已有通報資料時顯示） */
export function IncidentReportPanel({ inc }: { inc: Incident }) {
  const relevant = inc.final_severity === "S3" || !!inc.incident_report || !!inc.casualty;
  const q = useAsync(() => (relevant ? api.incidentReport(inc.id) : Promise.resolve(null)), [inc.id, relevant]);
  const reload = useThrottled(q.reload, 300);
  useWs(
    ["incidents"],
    (m) => {
      const d = (m.data ?? {}) as { incident_id?: string; payload?: { incident_id?: string } };
      if (d.incident_id === inc.id || d.payload?.incident_id === inc.id) reload();
    },
    relevant,
  );
  const rep = q.data;
  const left = useRemaining(rep?.deadline, rep?.remaining_s);
  if (!relevant) return null;
  if (q.error) return <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">無法取得職災通報狀態：{q.error.message}</div>;
  if (!rep) return null;
  const required = rep.status === "draft" || rep.status === "overdue";
  const overdue = rep.status === "overdue" || (left !== null && left <= 0);
  return (
    <Card className={cn(required && "border-2 border-red-500")}>
      <CardHeader
        title="職業災害 8 小時通報（職安法第37條第2項）"
        icon={<AlertTriangle className="h-4 w-4 text-red-400" />}
        right={
          required ? (
            <span className={cn("tabular font-mono text-lg font-bold", overdue ? "text-red-300" : "text-white")}>
              {overdue ? "已逾期" : `剩 ${left !== null ? fmtDuration(left) : "—"}`}
            </span>
          ) : rep.status === "submitted" ? (
            <span className="text-sm text-emerald-300">已送出</span>
          ) : null
        }
      />
      <CardContent className="space-y-3">
        {required && (
          <div className="rounded-md border border-red-500/60 bg-red-600/20 px-3 py-2">
            <div className="text-base font-bold text-amber-200">⚠ {SCENE_NOTICE}</div>
            <div className="mt-0.5 text-sm text-red-100">
              符合：{rep.criteria_met.join("、")}・期限 {fmtDateTime(rep.deadline)}
            </div>
            {rep.instructions.filter((x) => x !== SCENE_NOTICE).map((x, i) => (
              <div key={i} className="text-xs text-red-200">
                {x}
              </div>
            ))}
          </div>
        )}
        {rep.status === "none" && !rep.casualty && <CasualtyForm incidentId={inc.id} onDone={(r) => q.setData(() => r)} />}
        {rep.status === "none" && rep.casualty && (
          <div className="text-sm text-muted">
            已標記傷亡（死亡 {rep.casualty.deaths}、罹災 {rep.casualty.injured}、住院 {rep.casualty.hospitalized}），未達第37條第2項通報條件。
          </div>
        )}
        {required && <DraftEditor rep={rep} onDone={(r) => q.setData(() => r)} />}
        {rep.status === "submitted" && (
          <div className="flex items-center gap-2 text-sm text-emerald-300">
            <CheckCircle2 className="h-4 w-4" /> 已由 {rep.submitted_by} 於 {fmtDateTime(rep.submitted_at)} 送出通報
          </div>
        )}
      </CardContent>
    </Card>
  );
}
