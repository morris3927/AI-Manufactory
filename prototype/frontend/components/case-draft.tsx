"use client";

import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import type { CaseDraftInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 草稿是誰整理的：LLM 整理／程式組稿（LLM 整理中、失敗時保留程式版） */
export function CaseDraftBadge({ info }: { info: CaseDraftInfo }) {
  const llm = info.drafted_by === "llm";
  const label = llm
    ? "LLM 整理"
    : info.llm_status === "pending"
      ? "程式組稿・LLM 整理中…"
      : info.llm_status === "failed"
        ? "程式組稿（LLM 整理失敗）"
        : "程式組稿";
  return (
    <span className={cn("rounded px-1.5 py-px text-xs", llm ? "bg-violet-500/20 text-violet-200" : "bg-panel-2 text-muted")}>{label}</span>
  );
}

/** 可展開的草稿全文：摘要、原因、處置步驟（最後一項是驗證）、注意事項 */
export function CaseDraftDetail({ info, report, className }: { info: CaseDraftInfo; report?: string | null; className?: string }) {
  const c = info.content;
  if (!c) return null;
  const actions = c.actions ?? [];
  return (
    <details className={cn("group w-full", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-1 text-xs text-muted hover:text-foreground">
        <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" /> 草稿全文（摘要、原因、處置步驟、注意事項）
      </summary>
      <div className="mt-1.5 space-y-2 rounded bg-background/60 p-2.5 text-sm leading-relaxed text-foreground/90">
        <Section title="摘要">{c.summary || "—"}</Section>
        <Section title="原因">
          {c.evidence?.length ? (
            <ul className="list-disc space-y-0.5 pl-5">
              {c.evidence.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          ) : (
            "—"
          )}
          {c.why_first_guess_wrong && <div className="mt-1 text-amber-200">第一判斷為什麼不對：{c.why_first_guess_wrong}</div>}
        </Section>
        <Section title="處置步驟與驗證">
          {actions.length ? (
            <ol className="list-decimal space-y-0.5 pl-5">
              {actions.map((a, i) => (
                <li key={i}>
                  {a.step}
                  {a.result && <span className="text-muted"> → {a.result}</span>}
                </li>
              ))}
            </ol>
          ) : (
            <span className="text-muted">（程式組稿沒有拆步驟，請看回報原文）</span>
          )}
        </Section>
        <Section title="注意事項">{c.lessons || "—"}</Section>
        {!!c.sop_used?.length && <Section title="使用 SOP">{c.sop_used.join("、")}</Section>}
        {report && <Section title="技術員回報原文">{report}</Section>}
      </div>
    </details>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-xs font-semibold text-muted">{title}</div>
      <div>{children}</div>
    </div>
  );
}
