"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowUpCircle, ClipboardList, User } from "lucide-react";
import { Countdown, Elapsed } from "@/components/countdown";
import { ErrorNote } from "@/components/error-note";
import { SeverityBadge } from "@/components/severity";
import { SimBadge } from "@/components/sim-badge";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useAsync, useThrottled } from "@/lib/hooks";
import { CATEGORY_LABEL, KANBAN_COLUMNS, LINE_LABEL, PERSON_ROLE_LABEL, SEVERITY_STYLE, STATUS_LABEL, kanbanColumnOf } from "@/lib/labels";
import type { WorkOrder } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/ws";

const COL_TONE: Record<string, string> = {
  todo: "text-red-300",
  doing: "text-amber-300",
  verify: "text-cyan-300",
  done: "text-emerald-300",
};

export default function KanbanPage() {
  const wos = useAsync(() => api.workorders(), []);
  const plant = useAsync(() => api.plant(), []);
  const [line, setLine] = useState<string>("all");
  const [flash, setFlash] = useState<Record<string, number>>({});
  const reload = useThrottled(wos.reload, 250);

  useWs(["workorders"], (m) => {
    const d = m.data as { id?: string; workorder?: WorkOrder };
    const id = d?.workorder?.id ?? d?.id;
    if (id) setFlash((f) => ({ ...f, [id]: Date.now() }));
    reload();
  });

  const list = (wos.data ?? []).filter((w) => line === "all" || w.line === line);
  const lines = plant.data?.lines ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ClipboardList className="h-5 w-5 text-accent" />
        <h1 className="text-lg font-semibold">任務看板</h1>
        <SimBadge />
        <div className="ml-auto flex flex-wrap gap-1">
          {[{ id: "all", name: "全部產線" }, ...lines].map((l) => (
            <button
              key={l.id}
              onClick={() => setLine(l.id)}
              className={cn("rounded-md border px-2.5 py-1 text-xs", line === l.id ? "border-cyan-500 bg-cyan-500/15 text-cyan-200" : "border-line text-muted hover:text-foreground")}
            >
              {l.name}
            </button>
          ))}
        </div>
      </div>
      {wos.error && <ErrorNote e={wos.error} />}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {KANBAN_COLUMNS.map((col) => {
          const items = list.filter((w) => kanbanColumnOf(w.status) === col.id);
          return (
            <Card key={col.id} className="flex min-h-[200px] flex-col bg-panel/70">
              <div className="flex items-center gap-2 border-b border-line px-3 py-2">
                <span className={cn("text-sm font-semibold", COL_TONE[col.id])}>{col.label}</span>
                <span className="rounded bg-panel-2 px-1.5 text-xs text-muted">{items.length}</span>
                <span className="ml-auto text-[11px] text-muted">{col.statuses.map((s) => STATUS_LABEL[s]).join("／")}</span>
              </div>
              <div className="flex-1 space-y-2 p-2">
                {items.map((w) => (
                  <KanbanCard key={`${w.id}-${flash[w.id] ?? 0}`} w={w} flash={!!flash[w.id]} />
                ))}
                {!items.length && <div className="py-6 text-center text-xs text-muted">—</div>}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function KanbanCard({ w, flash }: { w: WorkOrder; flash: boolean }) {
  const sev = w.severity ?? "S1";
  const done = w.status === "CLOSED" || w.status === "CANCELLED";
  return (
    <Link
      href={`/workorders/${w.id}`}
      className={cn(
        "relative block overflow-hidden rounded-md border border-line bg-panel-2/70 py-2 pl-3.5 pr-2.5 transition-colors hover:border-cyan-500/60",
        flash && "flash-in",
        done && "opacity-60",
      )}
    >
      <span className={cn("absolute inset-y-0 left-0 w-1.5", SEVERITY_STYLE[sev].bar)} />
      <div className="flex items-center gap-1.5">
        <SeverityBadge s={sev} short />
        <span className="font-mono text-xs text-muted">{w.id}</span>
        <span className="ml-auto rounded bg-black/30 px-1.5 py-0.5 text-[11px] text-slate-300">{STATUS_LABEL[w.status]}</span>
      </div>
      <div className="mt-1.5 text-sm font-medium leading-snug">{w.title ?? "異常"}</div>
      <div className="mt-0.5 text-xs text-muted">
        {LINE_LABEL[w.line ?? ""] ?? w.line}・<span className="font-mono">{w.machine_id}</span>・{CATEGORY_LABEL[w.category ?? "equipment"]}
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs">
        <User className="h-3.5 w-3.5 text-muted" />
        <span>{w.assignee_name ?? w.assignee_id ?? "未指派"}</span>
        <span className="text-muted">{PERSON_ROLE_LABEL[w.assignee_role ?? ""] ?? w.assignee_role}</span>
        {w.escalation_level > 0 && (
          <span className="ml-auto flex items-center gap-0.5 rounded bg-red-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-red-300" title="升級次數">
            <ArrowUpCircle className="h-3 w-3" /> 升級 {w.escalation_level}
          </span>
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-2 border-t border-line/60 pt-1.5">
        <span className="text-[11px] text-muted">{done ? "處理時間" : "經過"}</span>
        <Elapsed since={w.created_at} until={w.closed_at} />
        {(w.status === "ASSIGNED" || w.status === "ESCALATED") && <Countdown deadline={w.ack_deadline} className="ml-auto" />}
      </div>
    </Link>
  );
}
