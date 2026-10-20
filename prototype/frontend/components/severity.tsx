import { SEVERITY_LABEL, SEVERITY_STYLE } from "@/lib/labels";
import type { Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SeverityBadge({ s, short, className }: { s: Severity | undefined; short?: boolean; className?: string }) {
  if (!s) return null;
  const st = SEVERITY_STYLE[s];
  return (
    <span className={cn("inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-bold leading-none", st.bg, st.text, className)}>
      {short ? s : SEVERITY_LABEL[s]}
    </span>
  );
}

export function HealthDot({ h, className }: { h?: string; className?: string }) {
  const c =
    h === "alarm" ? "bg-red-500 pulse-red" : h === "warn" ? "bg-amber-400" : h === "offline" ? "bg-slate-600" : "bg-emerald-500 shadow-[0_0_8px_rgba(34,197,94,0.7)]";
  return <span className={cn("inline-block h-2.5 w-2.5 shrink-0 rounded-full", c, className)} />;
}
