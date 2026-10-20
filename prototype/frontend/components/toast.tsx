"use client";

import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

interface Toast {
  id: number;
  text: string;
  tone: "error" | "ok" | "info";
}
let toasts: Toast[] = [];
let seq = 0;
const ls = new Set<() => void>();
const notify = () => ls.forEach((l) => l());

export function toast(text: string, tone: Toast["tone"] = "info") {
  const t = { id: ++seq, text, tone };
  toasts = [...toasts, t].slice(-4);
  notify();
  setTimeout(() => {
    toasts = toasts.filter((x) => x.id !== t.id);
    notify();
  }, 4000);
}

const EMPTY: Toast[] = [];

export function Toaster() {
  const list = useSyncExternalStore(
    (l) => {
      ls.add(l);
      return () => {
        ls.delete(l);
      };
    },
    () => toasts,
    () => EMPTY,
  );
  return (
    <div className="pointer-events-none fixed bottom-12 left-1/2 z-[60] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2">
      {list.map((t) => (
        <div
          key={t.id}
          className={cn(
            "rounded-md border px-3 py-2 text-sm shadow-lg backdrop-blur",
            t.tone === "error" && "border-red-500/60 bg-red-950/90 text-red-100",
            t.tone === "ok" && "border-emerald-500/60 bg-emerald-950/90 text-emerald-100",
            t.tone === "info" && "border-cyan-500/60 bg-slate-900/90 text-cyan-100",
          )}
        >
          {t.text}
        </div>
      ))}
    </div>
  );
}
