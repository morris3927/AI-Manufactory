"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { FileSearch, Search } from "lucide-react";
import { ErrorNote } from "@/components/error-note";
import { SeverityBadge } from "@/components/severity";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { AuditList } from "@/components/workorder/panels";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { cn, fmtTime } from "@/lib/utils";

export function AuditView() {
  const sp = useSearchParams();
  const router = useRouter();
  const trace = sp.get("trace_id") ?? "";
  const woId = sp.get("workorder_id") ?? "";
  const [q, setQ] = useState(trace || woId);
  const incidents = useAsync(() => api.incidents(), []);
  const wos = useAsync(() => api.workorders(), []);
  const audit = useAsync(() => api.audit(trace ? { trace_id: trace } : woId ? { workorder_id: woId } : {}), [trace, woId]);

  const go = (v: string) => {
    const s = v.trim();
    if (!s) return router.push("/audit");
    router.push(s.toUpperCase().startsWith("WO-") ? `/audit?workorder_id=${encodeURIComponent(s)}` : `/audit?trace_id=${encodeURIComponent(s)}`);
  };
  const woOf = (incId: string) => wos.data?.find((w) => w.incident_id === incId);

  return (
    <div className="grid gap-3 lg:grid-cols-[360px_minmax(0,1fr)]">
      <Card className="flex max-h-[calc(100vh-6rem)] flex-col">
        <CardHeader title="異常事件" icon={<FileSearch className="h-4 w-4" />} />
        <div className="border-b border-line p-2">
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              go(q);
            }}
          >
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="trace_id 或工單編號（WO-…）" className="h-9 min-w-0 flex-1 rounded-md border border-line bg-panel-2 px-2 font-mono text-sm" />
            <Button type="submit" variant="primary" aria-label="查詢">
              <Search className="h-4 w-4" />
            </Button>
          </form>
        </div>
        <div className="min-h-0 flex-1 divide-y divide-line/60 overflow-y-auto">
          {(incidents.data ?? []).map((i) => {
            const w = woOf(i.id);
            return (
              <button key={i.id} onClick={() => (setQ(i.trace_id), go(i.trace_id))} className={cn("block w-full px-3 py-2 text-left hover:bg-panel-2", trace === i.trace_id && "bg-cyan-500/10")}>
                <div className="flex items-center gap-2">
                  <SeverityBadge s={i.final_severity} short />
                  <span className="truncate text-sm">{i.title ?? i.type}</span>
                  <span className="tabular ml-auto font-mono text-[11px] text-muted">{fmtTime(i.opened_at)}</span>
                </div>
                <div className="mt-0.5 font-mono text-[11px] text-muted">
                  {i.trace_id}
                  {w && ` ・${w.id}`}
                </div>
              </button>
            );
          })}
        </div>
      </Card>
      <Card>
        <CardHeader title={trace ? `trace_id ${trace}` : woId ? `工單 ${woId}` : "最近稽核紀錄"} right={<span className="text-xs text-muted">只新增、不修改</span>} />
        <CardContent>
          {audit.error ? <ErrorNote e={audit.error} /> : <AuditList list={trace || woId ? (audit.data ?? []) : [...(audit.data ?? [])].reverse()} />}
        </CardContent>
      </Card>
    </div>
  );
}
