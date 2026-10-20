"use client";

import Link from "next/link";
import { useState } from "react";
import { Clapperboard, Play, RotateCcw, Settings2, Timer, Video, Zap } from "lucide-react";
import { ErrorNote } from "@/components/error-note";
import { toast } from "@/components/toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { usePersona } from "@/lib/session";
import type { DemoSettings, SentinelEvent, WorkOrder } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";
import { useWs } from "@/lib/ws";

export default function ControlPage() {
  const ro = usePersona().role === "judge";
  const scenarios = useAsync(() => api.scenarios(), []);
  const plant = useAsync(() => api.plant(), []);
  const videos = useAsync(() => api.videos(), []);
  const settings = useAsync(() => api.settings(), []);
  const [log, setLog] = useState<{ ts: string; text: string; href?: string }[]>([]);
  const [pick, setPick] = useState<Record<string, string>>({});
  const [timeout, setTimeoutS] = useState<number | null>(null);
  const [running, setRunning] = useState<string | null>(null);

  useWs(["events", "workorders"], (m) => {
    if (m.channel === "events") {
      const ev = m.data as SentinelEvent;
      if (ev.type === "mes_log") return;
      const p = ev.payload as Record<string, unknown>;
      const text =
        ev.type === "camera_source"
          ? `${ev.camera_id} 切換影片 → ${String(p.video).split("/").pop()}`
          : ev.type === "incident_opened"
            ? `異常成立：${p.title}（${p.severity}）`
            : `${ev.source} ${ev.type} ${ev.camera_id ?? ev.machine_id ?? ""}`;
      setLog((l) => [{ ts: ev.ts, text }, ...l].slice(0, 40));
      if (ev.type === "camera_source") plant.reload();
    } else if (m.type === "created" || m.type === "escalated") {
      const w = (m.data as { workorder?: WorkOrder }).workorder ?? (m.data as WorkOrder);
      setLog((l) => [{ ts: m.ts, text: `${m.type === "created" ? "建立工單" : "工單升級"} ${w.id}：${w.title ?? ""} → ${w.assignee_name ?? w.assignee_id}`, href: `/workorders/${w.id}` }, ...l].slice(0, 40));
    }
  });

  const run = async (id: string) => {
    setRunning(id);
    try {
      const r = (await api.runScenario(id)) as { deduped?: boolean };
      if (r?.deduped) toast("同機台已有未結案的同類異常（RU-6 去重，只更新原事件）。要重新展示請先「重置 Demo」。", "info");
      else toast("劇本已啟動：影片與 Log 同步送出", "ok");
    } catch (e) {
      toast(String((e as Error).message), "error");
    } finally {
      setTimeout(() => setRunning(null), 1500);
    }
  };

  const saveSettings = async (s: Partial<DemoSettings>) => {
    try {
      const r = await api.updateSettings(s);
      settings.setData(() => r);
      toast("設定已更新", "ok");
    } catch (e) {
      toast(`更新失敗：${(e as Error).message}`, "error");
    }
  };

  const cams = (plant.data?.lines ?? []).flatMap((l) => l.cameras.map((c) => ({ ...c, lineName: l.name, line: l.id })));

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-3">
        <Card>
          <CardHeader title="劇本" icon={<Clapperboard className="h-4 w-4" />} right={<span className="text-xs text-muted">按鈕只負責送影片與 Log，判斷全部即時運算</span>} />
          <CardContent className="grid gap-2 sm:grid-cols-2">
            {scenarios.error && <ErrorNote e={scenarios.error} />}
            {(scenarios.data ?? []).map((s) => (
              <button
                key={s.id}
                disabled={ro || running === s.id}
                onClick={() => run(s.id)}
                className={cn(
                  "group flex items-start gap-3 rounded-lg border border-line bg-panel-2/60 p-3 text-left transition-colors hover:border-cyan-500 disabled:opacity-50",
                  running === s.id && "border-cyan-400 bg-cyan-500/10",
                )}
              >
                <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-cyan-500/15 text-cyan-300 group-hover:bg-cyan-500 group-hover:text-slate-950">
                  <Play className="h-4 w-4" />
                </span>
                <span>
                  <span className="block font-semibold">{s.title}</span>
                  {s.description && <span className="mt-0.5 block text-xs text-muted">{s.description}</span>}
                </span>
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="攝影機模擬器" icon={<Video className="h-4 w-4" />} right={<span className="text-xs text-muted">把影片指定給虛擬攝影機，以真實速度逐幀送出</span>} />
          <CardContent className="space-y-2">
            {videos.error && <div className="text-xs text-amber-300">無法取得影片清單（/api/videos）：{videos.error.message}</div>}
            {cams.map((c) => {
              const opts = (videos.data ?? []).filter((v) => !v.line || v.line === c.line);
              const val = pick[c.id] ?? c.source ?? "";
              return (
                <div key={c.id} className="flex flex-wrap items-center gap-2 rounded border border-line bg-panel-2/40 px-2 py-2">
                  <div className="w-40 shrink-0">
                    <div className="font-mono text-sm">{c.id}</div>
                    <div className="text-[11px] text-muted">{c.name ?? c.lineName}</div>
                  </div>
                  <select
                    value={val}
                    onChange={(e) => setPick((p) => ({ ...p, [c.id]: e.target.value }))}
                    className="h-9 min-w-0 flex-1 rounded-md border border-line bg-panel px-2 text-sm"
                  >
                    {!opts.some((o) => o.path === val) && val && <option value={val}>{val.split("/").pop()}</option>}
                    {opts.map((v) => (
                      <option key={v.path} value={v.path}>
                        {v.label}（{v.path.split("/").pop()}）
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="primary"
                    disabled={ro || !val || val === c.source}
                    onClick={async () => {
                      try {
                        await api.setCameraSource(c.id, val);
                        toast(`${c.id} 開始播放 ${val.split("/").pop()}`, "ok");
                        plant.reload();
                      } catch (e) {
                        toast((e as Error).message, "error");
                      }
                    }}
                  >
                    播放
                  </Button>
                  <span className="w-full truncate font-mono text-[10px] text-muted sm:w-auto">目前：{c.source?.split("/").pop() ?? "—"}</span>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="Demo 設定" icon={<Settings2 className="h-4 w-4" />} sim={false} />
          <CardContent className="grid gap-3 sm:grid-cols-3">
            {settings.error && <div className="text-xs text-amber-300 sm:col-span-3">無法讀取設定（/api/demo/settings）：{settings.error.message}</div>}
            <div className="rounded border border-line p-3">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Timer className="h-4 w-4 text-accent" /> 升級逾時（秒）
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  type="number"
                  min={5}
                  max={600}
                  value={timeout ?? settings.data?.escalation_timeout_s ?? 30}
                  onChange={(e) => setTimeoutS(Number(e.target.value))}
                  className="h-9 w-24 rounded-md border border-line bg-panel-2 px-2 text-sm"
                />
                <Button disabled={ro} onClick={() => saveSettings({ escalation_timeout_s: timeout ?? settings.data?.escalation_timeout_s ?? 30 })}>
                  套用
                </Button>
              </div>
              <p className="mt-1 text-[11px] text-muted">正式值 S2 5 分、S3 2 分；Demo 建議 30 秒</p>
            </div>
            <div className="rounded border border-line p-3">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <Zap className="h-4 w-4 text-accent" /> Jev 直覺層
              </div>
              <button
                disabled={ro || !settings.data}
                onClick={() => saveSettings({ jev_enabled: !settings.data?.jev_enabled })}
                className={cn("mt-2 flex h-9 w-full items-center justify-between rounded-md border px-3 text-sm", settings.data?.jev_enabled ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-200" : "border-red-500/60 bg-red-500/10 text-red-200")}
              >
                {settings.data?.jev_enabled ? "開啟" : "關閉（展示退路）"}
                <span className={cn("h-5 w-9 rounded-full p-0.5 transition-colors", settings.data?.jev_enabled ? "bg-emerald-500" : "bg-slate-600")}>
                  <span className={cn("block h-4 w-4 rounded-full bg-white transition-transform", settings.data?.jev_enabled && "translate-x-4")} />
                </span>
              </button>
              <p className="mt-1 text-[11px] text-muted">關閉後規則照常建單派工，通知註明「AI 判斷暫不可用」</p>
            </div>
            <div className="rounded border border-line p-3">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <RotateCcw className="h-4 w-4 text-accent" /> 重置 Demo
              </div>
              <Button
                variant="danger"
                className="mt-2 w-full"
                disabled={ro}
                onClick={async () => {
                  if (!window.confirm("確定重置？未結案工單與事件會清除。")) return;
                  try {
                    await api.resetDemo();
                    setLog([]);
                    plant.reload();
                    toast("Demo 已重置", "ok");
                  } catch (e) {
                    toast((e as Error).message, "error");
                  }
                }}
              >
                重置
              </Button>
              <p className="mt-1 text-[11px] text-muted">清除進行中的異常，攝影機回到正常影片</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="flex max-h-[calc(100vh-6rem)] flex-col">
        <CardHeader title="系統回應" right={<span className="text-xs text-muted">WebSocket 即時</span>} />
        <div className="min-h-0 flex-1 divide-y divide-line/60 overflow-y-auto">
          {log.map((l, i) => (
            <div key={i} className="flash-in flex gap-2 px-3 py-1.5 text-xs">
              <span className="tabular shrink-0 font-mono text-muted">{fmtTime(l.ts)}</span>
              {l.href ? (
                <Link href={l.href} className="text-cyan-300 hover:underline">
                  {l.text}
                </Link>
              ) : (
                <span className="text-slate-300">{l.text}</span>
              )}
            </div>
          ))}
          {!log.length && <div className="p-4 text-center text-xs text-muted">按劇本或切換影片後，這裡會顯示系統即時反應</div>}
        </div>
      </Card>
    </div>
  );
}
