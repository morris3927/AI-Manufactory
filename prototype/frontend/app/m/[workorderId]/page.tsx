"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { ErrorNote } from "@/components/error-note";
import { RootCauseBars } from "@/components/charts";
import { SimBadge } from "@/components/sim-badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { AgentChat, ChecklistPanel, LotoPanel, PermitPanel, ReportPanel, WorkorderHeader } from "@/components/workorder/panels";
import { useWorkorder } from "@/components/workorder/use-workorder";
import { IncidentReportPanel } from "@/components/incident-report";
import { reached } from "@/lib/labels";
import { cn } from "@/lib/utils";

type Tab = "loto" | "agent" | "check" | "report";

export default function MobileWorkorderPage() {
  const { workorderId } = useParams<{ workorderId: string }>();
  const { wo, error, streaming, act, busy } = useWorkorder(workorderId);
  const [tab, setTab] = useState<Tab | null>(null);
  if (error) return <ErrorNote e={error} />;
  if (!wo) return <div className="p-8 text-center text-sm text-muted">載入工單…</div>;
  const safety = wo.category === "safety";
  // 依狀態自動帶到該做的那一步（使用者點了分頁就以使用者為準）
  const auto: Tab = safety
    ? wo.status === "ACKED" && wo.checklist?.every((c) => c.checked_by)
      ? "report"
      : "check"
    : !reached(wo.status, "IN_REPAIR")
      ? "loto"
      : wo.status === "IN_REPAIR"
        ? wo.checklist?.every((c) => c.checked_by)
          ? "check"
          : "agent"
        : "report";
  const cur = tab ?? auto;
  const tabs: { id: Tab; label: string }[] = [
    ...(safety ? [] : [{ id: "loto" as Tab, label: "許可／LOTO" }]),
    { id: "agent", label: "Agent" },
    { id: "check", label: "Checklist" },
    { id: "report", label: "回報" },
  ];
  const top = (wo.diagnosis ?? wo.incident.diagnosis)?.root_cause_top3 ?? [];
  return (
    <div className="mx-auto max-w-xl space-y-3 pb-16">
      <WorkorderHeader wo={wo} act={act} busy={busy} compact />
      <IncidentReportPanel inc={wo.incident} />
      {top.length > 0 && (
        <Card>
          <CardHeader title="根因前三名" />
          <CardContent className="py-1">
            <RootCauseBars items={top} height={120} />
          </CardContent>
        </Card>
      )}
      <div className="sticky top-12 z-30 -mx-3 flex gap-1 bg-background/95 px-3 py-2 backdrop-blur">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn("h-10 flex-1 rounded-md border text-sm", cur === t.id ? "border-cyan-500 bg-cyan-500/15 text-cyan-200" : "border-line text-muted")}
          >
            {t.label}
            {t.id === auto && <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-cyan-400 align-middle" />}
          </button>
        ))}
      </div>
      {cur === "loto" && (
        <>
          <PermitPanel wo={wo} act={act} busy={busy} />
          <LotoPanel wo={wo} act={act} busy={busy} big />
        </>
      )}
      {cur === "agent" && (
        <>
          <AgentChat wo={wo} act={act} busy={busy} streaming={streaming} tall />
        </>
      )}
      {cur === "check" && <ChecklistPanel wo={wo} act={act} busy={busy} big />}
      {cur === "report" && <ReportPanel wo={wo} act={act} busy={busy} />}
      <div className="text-center">
        <SimBadge />
      </div>
    </div>
  );
}
