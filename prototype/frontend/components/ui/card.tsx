import { cn } from "@/lib/utils";
import type { HTMLAttributes, ReactNode } from "react";
import { SimBadge } from "../sim-badge";

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-line bg-panel/90 shadow-[0_0_0_1px_rgba(0,0,0,0.3)] backdrop-blur", className)} {...p} />;
}

export function CardHeader({
  title,
  icon,
  right,
  sim = true,
  className,
}: {
  title: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  /** 顯示「模擬資料」標籤（競賽規定） */
  sim?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2 border-b border-line px-3 py-2", className)}>
      {icon && <span className="text-accent">{icon}</span>}
      <h2 className="text-[15px] font-semibold tracking-wide text-foreground">{title}</h2>
      {sim && <SimBadge />}
      <div className="ml-auto flex items-center gap-2">{right}</div>
    </div>
  );
}

export function CardContent({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-3", className)} {...p} />;
}
