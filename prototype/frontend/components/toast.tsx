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
            // 不透明的 bg-panel 疊一層同色系淡底；文字色在淺色模式會換成深色階
            "rounded-md border bg-panel px-3 py-2 text-sm shadow-lg",
            t.tone === "error" && "border-red-500/60 bg-[linear-gradient(rgb(239_68_68/0.14),rgb(239_68_68/0.14))] text-red-100",
            t.tone === "ok" && "border-emerald-500/60 bg-[linear-gradient(rgb(16_185_129/0.14),rgb(16_185_129/0.14))] text-emerald-100",
            t.tone === "info" && "border-cyan-500/60 text-cyan-100",
          )}
        >
          {t.text}
        </div>
      ))}
    </div>
  );
}
