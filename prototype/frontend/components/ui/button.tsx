import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "warn" | "ok";
type Size = "sm" | "md" | "lg";

const V: Record<Variant, string> = {
  primary: "bg-cyan-500 text-slate-950 hover:bg-cyan-400 font-semibold",
  secondary: "bg-panel-2 text-foreground border border-line hover:border-cyan-500/60",
  ghost: "text-muted hover:text-foreground hover:bg-panel-2",
  danger: "bg-red-600 text-white hover:bg-red-500 font-semibold",
  warn: "bg-amber-500 text-slate-950 hover:bg-amber-400 font-semibold",
  ok: "bg-emerald-600 text-white hover:bg-emerald-500 font-semibold",
};
const SZ: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-9 px-3.5 text-sm",
  lg: "h-12 px-5 text-base",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...p
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        V[variant],
        SZ[size],
        className,
      )}
      {...p}
    />
  );
}
