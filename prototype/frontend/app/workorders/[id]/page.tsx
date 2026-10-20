"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Smartphone } from "lucide-react";
import { ErrorNote } from "@/components/error-note";
import { AgentChat, AuditTimeline, ChecklistPanel, DiagnosisPanel, EventInfo, LotoPanel, PermitPanel, ReportPanel, WorkorderHeader } from "@/components/workorder/panels";
import { useWorkorder } from "@/components/workorder/use-workorder";

export default function WorkorderPage() {
  const { id } = useParams<{ id: string }>();
  const { wo, error, streaming, act, busy } = useWorkorder(id);
  if (error) return <ErrorNote e={error} />;
  if (!wo) return <div className="p-8 text-center text-sm text-muted">載入工單 {id}…</div>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-muted">
        <Link href="/kanban" className="hover:text-foreground">
          任務看板
        </Link>
        <span>›</span>
        <span className="font-mono">{wo.id}</span>
        <Link href={`/m/${wo.id}`} className="ml-auto flex items-center gap-1 rounded border border-line px-2 py-1 hover:text-foreground">
          <Smartphone className="h-3.5 w-3.5" /> 技術員手機版
        </Link>
      </div>
      <WorkorderHeader wo={wo} act={act} busy={busy} />
      <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)_minmax(0,1.1fr)]">
        <div className="min-w-0">
          <EventInfo wo={wo} />
        </div>
        <div className="min-w-0">
          <DiagnosisPanel wo={wo} />
        </div>
        <div className="min-w-0 space-y-3 lg:col-span-2 2xl:col-span-1">
          <LotoPanel wo={wo} act={act} busy={busy} />
          <PermitPanel wo={wo} act={act} busy={busy} />
          <AgentChat wo={wo} act={act} busy={busy} streaming={streaming} />
          <ChecklistPanel wo={wo} act={act} busy={busy} />
          <ReportPanel wo={wo} act={act} busy={busy} />
        </div>
      </div>
      <AuditTimeline traceId={wo.trace_id ?? wo.incident.trace_id} refreshKey={`${wo.status}-${wo.agent_messages?.length ?? 0}-${wo.loto?.steps.filter((s) => s.checked_by).length ?? 0}`} />
    </div>
  );
}
