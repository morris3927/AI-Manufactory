"use client";

import { useState } from "react";
import { Maximize2, Minimize2, VideoOff } from "lucide-react";
import { api } from "@/lib/api";
import { MOCK } from "@/lib/config";
import { useNow } from "@/lib/hooks";
import type { Box, Camera } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MockFeed } from "./mock-feed";
import { SimBadge } from "./sim-badge";

export interface Overlay {
  box: Box;
  label: string;
  /** jev = Jev Box（青色）；yolo = YOLO 偵測（紅色）；roi = 感興趣區域 */
  kind: "jev" | "yolo" | "roi";
}

const KIND: Record<Overlay["kind"], string> = {
  jev: "border-cyan-300 shadow-[0_0_12px_rgba(34,211,238,0.8)]",
  yolo: "border-red-500",
  roi: "border-dashed border-slate-400/50",
};
const KIND_LABEL: Record<Overlay["kind"], string> = {
  jev: "bg-cyan-300 text-slate-950",
  yolo: "bg-red-500 text-white",
  roi: "bg-slate-700/80 text-slate-200",
};

export function OverlayBoxes({ overlays }: { overlays: Overlay[] }) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {overlays.map((o, i) => {
        const [x1, y1, x2, y2] = o.box;
        return (
          <div
            key={i}
            className={cn("absolute rounded-sm border-2", KIND[o.kind])}
            style={{ left: `${(x1 / 1280) * 100}%`, top: `${(y1 / 720) * 100}%`, width: `${((x2 - x1) / 1280) * 100}%`, height: `${((y2 - y1) / 720) * 100}%` }}
          >
            <span className={cn("absolute -top-[18px] left-[-2px] whitespace-nowrap rounded-t-sm px-1 text-[10px] font-bold leading-[18px]", KIND_LABEL[o.kind])}>{o.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 單一攝影機畫面：真實模式用 MJPEG，模擬模式用 SVG */
export function CameraView({
  camera,
  overlays = [],
  frozen,
  className,
}: {
  camera: Camera;
  overlays?: Overlay[];
  frozen?: boolean;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const url = api.cameraStreamUrl(camera.id);
  return (
    <div className={cn("relative aspect-video w-full overflow-hidden bg-black", className)}>
      {MOCK || !url ? (
        <MockFeed cameraId={camera.id} source={camera.source} frozen={frozen} />
      ) : failed ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-[#0b0f14] text-red-400">
          <VideoOff className="h-8 w-8" />
          <span className="text-sm font-semibold">訊號中斷</span>
          <button className="pointer-events-auto text-xs text-muted underline" onClick={() => setFailed(false)}>
            重新連線
          </button>
        </div>
      ) : (
        <img src={url} alt={`${camera.id} 即時畫面`} className="h-full w-full object-fill" onError={() => setFailed(true)} />
      )}
      <OverlayBoxes overlays={overlays} />
    </div>
  );
}

export function CameraTile({
  camera,
  overlays,
  alert,
  expanded,
  onToggle,
  machineName,
}: {
  camera: Camera;
  overlays: Overlay[];
  alert?: string | null;
  expanded?: boolean;
  onToggle?: () => void;
  machineName?: string;
}) {
  const now = useNow();
  return (
    <div className={cn("group relative overflow-hidden rounded-lg border bg-black scanline", alert ? "border-red-500/80 pulse-red" : "border-line")}>
      <CameraView camera={camera} overlays={overlays} />
      {/* HUD */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-2 bg-gradient-to-b from-black/80 to-transparent px-2 py-1.5">
        <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
        <span className="font-mono text-xs font-semibold text-slate-100">{camera.id}</span>
        <span className="truncate text-xs text-slate-300">{camera.name ?? machineName}</span>
        <SimBadge className="ml-auto" />
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-2 bg-gradient-to-t from-black/80 to-transparent px-2 py-1.5">
        <span className="tabular font-mono text-[11px] text-slate-300">{now ? new Date(now).toLocaleTimeString("zh-TW", { hour12: false }) : ""}</span>
        <span className="truncate font-mono text-[10px] text-slate-500">{camera.source?.split("/").pop()}</span>
        {alert && <span className="ml-auto rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-bold text-white">{alert}</span>}
      </div>
      {onToggle && (
        <button
          onClick={onToggle}
          className="absolute right-2 top-8 rounded bg-black/60 p-1 text-slate-300 opacity-0 transition-opacity hover:text-white group-hover:opacity-100"
          aria-label={expanded ? "縮小" : "放大"}
        >
          {expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </button>
      )}
    </div>
  );
}
