"use client";

import Link from "next/link";
import { useState } from "react";
import { BookCheck } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync, useThrottled } from "@/lib/hooks";
import { usePersona } from "@/lib/session";
import { fmtDateTime } from "@/lib/utils";
import { useWs } from "@/lib/ws";
import { ErrorNote } from "./error-note";
import { toast } from "./toast";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader } from "./ui/card";

/** 知識回寫審核：結案產生的案例草稿，主管核准後才入庫（CL-3） */
export function KbReview() {
  const persona = usePersona();
  const isSup = persona.role === "supervisor";
  const [tab, setTab] = useState<"draft" | "approved">("draft");
  const q = useAsync(() => api.kbCases(tab), [tab]);
  const reload = useThrottled(q.reload, 500);
  const [busy, setBusy] = useState<string | null>(null);
  useWs(["workorders", "ai"], (m) => {
    if (m.type === "status_changed" || m.type === "case_approved" || m.type === "updated") reload();
  });
  const list = q.data ?? [];
  return (
    <Card>
      <CardHeader
        title="知識回寫審核"
        icon={<BookCheck className="h-4 w-4" />}
        right={
          <div className="flex gap-1">
            {(["draft", "approved"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`rounded px-2 py-0.5 text-xs ${tab === t ? "bg-cyan-500/20 text-cyan-200" : "text-muted hover:text-foreground"}`}>
                {t === "draft" ? "草稿" : "已入庫"}
              </button>
            ))}
          </div>
        }
      />
      <CardContent className="space-y-2">
        <p className="text-xs text-muted">結案回報通過 J5 後自動產生案例草稿；組長（主管）核准後才寫進向量庫，下次相似異常就會用上。</p>
        {q.error && <ErrorNote e={q.error} />}
        {list.map((c) => (
          <div key={c.case_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-line bg-panel-2/50 px-3 py-2">
            <span className="font-mono text-sm text-cyan-300">{c.case_id}</span>
            {c.workorder_id && (
              <Link href={`/workorders/${c.workorder_id}`} className="font-mono text-xs text-muted hover:text-foreground">
                {c.workorder_id}
              </Link>
            )}
            <span className="text-sm">{c.machine_id}</span>
            <span className="text-sm">
              根因：<span className="text-slate-100">{c.final_root_cause ?? "—"}</span>
              {c.hypothesis_correct === false && <span className="ml-1 text-amber-300">（第一判斷錯誤，可修正機率）</span>}
            </span>
            {c.reviewed_by && <span className="text-xs text-muted">審核 {c.reviewed_by}・{fmtDateTime(c.reviewed_at)}</span>}
            {tab === "draft" && (
              <Button
                size="sm"
                variant="ok"
                className="ml-auto"
                disabled={!isSup || busy === c.case_id}
                title={isSup ? undefined : "組長（主管）以上才能核准入庫"}
                onClick={async () => {
                  setBusy(c.case_id);
                  try {
                    const r = await api.approveCase(c.case_id, "現場主管核准");
                    toast(`${c.case_id} 已核准入庫（${r.ingested_chunks} 段）`, "ok");
                    q.reload();
                  } catch (e) {
                    toast((e as Error).message, "error");
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                {isSup ? "核准入庫" : "核准入庫（需主管）"}
              </Button>
            )}
            {c.report && <div className="w-full truncate text-xs text-muted">回報：{c.report}</div>}
          </div>
        ))}
        {!list.length && !q.error && <div className="py-3 text-center text-sm text-muted">{tab === "draft" ? "沒有待審核的草稿" : "尚無入庫案例"}</div>}
      </CardContent>
    </Card>
  );
}
