"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart3, ClipboardList, FileSearch, LayoutDashboard, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { MOCK } from "@/lib/config";
import { useNow } from "@/lib/hooks";
import { ROLE_LABEL } from "@/lib/labels";
import { PERSONAS, setRole, usePersona } from "@/lib/session";
import type { Role } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useWsStatus, wsClient } from "@/lib/ws";

const NAV = [
  { href: "/", label: "戰情儀表板", icon: LayoutDashboard },
  { href: "/kanban", label: "任務看板", icon: ClipboardList },
  { href: "/control", label: "控制台", icon: SlidersHorizontal },
  { href: "/metrics", label: "效益", icon: BarChart3 },
  { href: "/audit", label: "稽核", icon: FileSearch },
];

function Clock() {
  const now = useNow();
  return (
    <span className="tabular hidden font-mono text-xs text-muted lg:inline">
      {now ? new Date(now).toLocaleTimeString("zh-TW", { hour12: false }) : "--:--:--"}
    </span>
  );
}

function WsDot() {
  const s = useWsStatus();
  const map = {
    open: ["bg-emerald-500", "即時連線"],
    connecting: ["bg-amber-400 animate-pulse", "連線中"],
    closed: ["bg-red-500", "未連線"],
    mock: ["bg-fuchsia-400", "模擬事件"],
  } as const;
  const [c, t] = map[s];
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted" title={`WebSocket：${t}`}>
      <span className={cn("h-2 w-2 rounded-full", c)} />
      <span className="hidden sm:inline">{t}</span>
    </span>
  );
}

export function RoleSwitcher({ compact }: { compact?: boolean }) {
  const p = usePersona();
  return (
    <label className="flex items-center gap-1.5 text-xs">
      {!compact && <span className="hidden text-muted md:inline">身分</span>}
      <select
        value={p.role}
        onChange={(e) => {
          setRole(e.target.value as Role);
          wsClient.reconnect();
        }}
        className="h-8 rounded-md border border-line bg-panel-2 px-2 text-xs text-foreground focus:border-cyan-500 focus:outline-none"
        aria-label="切換身分"
      >
        {(Object.keys(PERSONAS) as Role[]).map((r) => (
          <option key={r} value={r}>
            {ROLE_LABEL[r]}・{PERSONAS[r].name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function AppHeader() {
  const path = usePathname();
  const mobile = path.startsWith("/m/");
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-[#070b10]/90 backdrop-blur">
      <div className="mx-auto flex h-12 max-w-[1800px] items-center gap-3 px-3 sm:px-4">
        <Link href="/" className="flex shrink-0 items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-cyan-400" />
          <span className="font-bold tracking-wider text-foreground">
            Sentinel <span className="text-cyan-400">AI</span>
          </span>
          <span className="hidden rounded bg-cyan-500/10 px-1.5 py-0.5 text-[10px] font-semibold tracking-widest text-cyan-300 xl:inline">戰情模式</span>
        </Link>
        {!mobile && (
          <nav className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = href === "/" ? path === "/" : path.startsWith(href) || (href === "/kanban" && path.startsWith("/workorders"));
              return (
                <Link
                  key={href}
                  href={href}
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                    active ? "bg-cyan-500/15 text-cyan-300" : "text-muted hover:bg-panel-2 hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span className="hidden md:inline">{label}</span>
                </Link>
              );
            })}
          </nav>
        )}
        {mobile && <span className="flex-1 truncate text-sm text-muted">技術員</span>}
        <div className="flex shrink-0 items-center gap-3">
          {MOCK && (
            <span className="hidden items-center gap-1 rounded border border-fuchsia-500/40 px-1.5 py-0.5 text-[10px] text-fuchsia-300 sm:flex" title="NEXT_PUBLIC_MOCK=1：前端內建模擬後端">
              <Activity className="h-3 w-3" /> 離線模擬
            </span>
          )}
          <Clock />
          <WsDot />
          <RoleSwitcher compact={mobile} />
        </div>
      </div>
    </header>
  );
}

/** 畫面右下角常駐的「模擬資料」標示 */
export function FloatingSimBadge() {
  return (
    <div className="pointer-events-none fixed bottom-3 right-3 z-50 rounded border border-fuchsia-400/60 bg-[#14081a]/90 px-2 py-1 text-xs font-semibold tracking-widest text-fuchsia-300 shadow-lg">
      模擬資料
    </div>
  );
}
