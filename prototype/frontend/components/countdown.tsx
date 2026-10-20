"use client";

import { Timer } from "lucide-react";
import { useNow } from "@/lib/hooks";
import { cn, fmtDuration } from "@/lib/utils";

/** 距離 deadline 的倒數（接單逾時升級） */
export function Countdown({ deadline, className }: { deadline: string | null | undefined; className?: string }) {
  const now = useNow();
  if (!deadline || !now) return null;
  const left = (new Date(deadline).getTime() - now) / 1000;
  const urgent = left < 15;
  return (
    <span className={cn("tabular inline-flex items-center gap-1 font-mono text-xs", urgent ? "text-red-400" : "text-amber-300", className)} title="接單逾時將逐級升級">
      <Timer className="h-3 w-3" />
      {left > 0 ? fmtDuration(left) : "升級中"}
    </span>
  );
}

/** 從 since 起算經過的時間 */
export function Elapsed({ since, until, className }: { since: string | null | undefined; until?: string | null; className?: string }) {
  const now = useNow();
  if (!since || !now) return null;
  const end = until ? new Date(until).getTime() : now;
  return <span className={cn("tabular font-mono text-xs text-muted", className)}>{fmtDuration((end - new Date(since).getTime()) / 1000)}</span>;
}
