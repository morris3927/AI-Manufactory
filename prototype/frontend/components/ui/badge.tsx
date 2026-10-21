import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

export function Badge({ className, ...p }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded border border-line bg-panel-2 px-1.5 py-0.5 text-xs leading-none text-muted", className)}
      {...p}
    />
  );
}
