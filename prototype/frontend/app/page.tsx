"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BellRing, Cpu, Factory, Gauge, Radio, Video } from "lucide-react";
import { CameraTile, type Overlay } from "@/components/camera-tile";
import { HandledRatio, SeverityBars } from "@/components/charts";
import { HealthDot, SeverityBadge } from "@/components/severity";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ErrorNote } from "@/components/error-note";
import { ReportAlertBar } from "@/components/incident-report";
import { api } from "@/lib/api";
import { useAsync, useNow, useThrottled } from "@/lib/hooks";
import { CATEGORY_LABEL, EVENT_TYPE_LABEL, STATUS_LABEL } from "@/lib/labels";
import type { Box, Camera, Incident, MetricsSummary, SentinelEvent, WorkOrder } from "@/lib/types";
import { cn, fmtTime, pct } from "@/lib/utils";
import { useWs } from "@/lib/ws";

const OVERLAY_TTL = 15000;
const FEED_TYPES = new Set(["jev_vision", "ppe_violation", "machine_alarm", "machine_state", "scenario_started", "camera_offline", "incident_opened", "camera_source"]);

const SIGNAL_PRIORITY: [string, string][] = [
  ["feed_rate", "m/min"],
  ["spm", "spm"],
  ["final_torque_nm", "N·m"],
  ["weld_current_a", "A"],
  ["air_pressure_bar", "bar"],
  ["cycle_time_s", "s"],
  ["motor_current_a", "A"],
  ["motor_temp_c", "°C"],
];

function KeySignal({ p }: { p?: Record<string, unknown> }) {
  const hit = p && SIGNAL_PRIORITY.find(([k]) => typeof p[k] === "number");
  if (!p || !hit) return <span className="text-muted">—</span>;
  const v = p[hit[0]] as number;
  return (
    <span className="tabular font-mono text-sm text-foreground">
      {Math.round(v * 100) / 100} <span className="text-xs text-muted">{hit[1]}</span>
    </span>
  );
}

export default function DashboardPage() {
  const plant = useAsync(() => api.plant(), []);
  const incidents = useAsync(() => api.incidents(), []);
  const workorders = useAsync(() => api.workorders(), []);
  const metrics = useAsync(() => api.metricsSummary(), []);
  const [liveMetrics, setLiveMetrics] = useState<MetricsSummary | null>(null);
  const [events, setEvents] = useState<SentinelEvent[]>([]);
  const [latest, setLatest] = useState<Record<string, Record<string, unknown>>>({});
  const [sources, setSources] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const buf = useRef<Record<string, Record<string, unknown>>>({});
  const now = useNow();

  const reloadPlant = useThrottled(plant.reload, 1500);
  const reloadInc = useThrottled(() => {
    incidents.reload();
    workorders.reload();
    plant.reload();
  }, 300);

  // MES Log 一秒多筆：先放 buffer，每秒更新一次畫面
  useEffect(() => {
    const id = setInterval(() => {
      if (Object.keys(buf.current).length) {
        const b = buf.current;
        buf.current = {};
        setLatest((prev) => ({ ...prev, ...b }));
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  useWs(["events", "incidents", "workorders", "ai", "metrics"], (m) => {
    if (m.channel === "events") {
      const ev = m.data as SentinelEvent;
      if (ev.type === "mes_log") {
        if (ev.machine_id) buf.current[ev.machine_id] = ev.payload;
        return;
      }
      if (ev.type === "camera_source" && ev.camera_id) {
        setSources((s) => ({ ...s, [ev.camera_id!]: String(ev.payload.video ?? "") }));
        reloadPlant();
      }
      if (ev.type === "machine_alarm") reloadPlant();
      setEvents((list) => [ev, ...list].slice(0, 80));
    } else if (m.channel === "metrics") {
      setLiveMetrics(m.data as MetricsSummary);
    } else if (m.channel === "incidents" || m.channel === "workorders") {
      reloadInc();
    }
  });

  const cameras: (Camera & { machineName?: string })[] = useMemo(
    () =>
      (plant.data?.lines ?? []).flatMap((l) =>
        l.cameras.map((c) => ({ ...c, line: l.id, source: sources[c.id] ?? c.source, machineName: l.machines.find((m) => m.id === c.machine_id)?.name })),
      ),
    [plant.data, sources],
  );

  const openIncidents = (incidents.data ?? []).filter((i) => i.status === "open");
  const woByIncident = useMemo(() => new Map((workorders.data ?? []).map((w) => [w.incident_id, w])), [workorders.data]);

  function overlaysFor(cam: Camera): Overlay[] {
    const out: Overlay[] = [];
    for (const ev of events) {
      if (ev.camera_id !== cam.id || !now || now - new Date(ev.ts).getTime() > OVERLAY_TTL) continue;
      const p = ev.payload as Record<string, unknown>;
      if (ev.type === "jev_vision" && Array.isArray(p.box)) {
        const prob = (p.p_jam ?? p.p_smoke ?? p.p_violation) as number | undefined;
        const what = p.p_jam !== undefined ? "卡料" : p.p_smoke !== undefined ? "煙霧" : "PPE";
        out.push({ box: p.box as Box, label: `Jev ${what} ${prob?.toFixed(2) ?? ""}`, kind: "jev" });
      }
      if (ev.type === "ppe_violation" && Array.isArray(p.person_box)) out.push({ box: p.person_box as Box, label: `${Array.isArray(p.missing) && p.missing.length ? "未戴安全帽" : "人員進入危險區"} ${(p.conf as number)?.toFixed(2)}`, kind: "yolo" });
    }
    for (const inc of openIncidents) {
      const st = woByIncident.get(inc.id)?.status;
      const fresh = !st || st === "OPEN" || st === "ASSIGNED" || st === "ESCALATED";
      if (fresh && inc.camera_id === cam.id && inc.jev_box && !out.some((o) => o.kind === "jev")) out.push({ box: inc.jev_box, label: `Jev Box・${inc.title ?? inc.type}`, kind: "jev" });
    }
    return out.slice(0, 4);
  }

  const m = liveMetrics ?? metrics.data;
  const sevCounts = m?.severity_counts ?? {};
  const pages = Math.max(1, Math.ceil(cameras.length / 4));
  const pg = Math.min(page, pages - 1);
  const shown = expanded ? cameras.filter((c) => c.id === expanded) : cameras.slice(pg * 4, pg * 4 + 4);
  const reportIncidents = (incidents.data ?? []).filter((i) => i.incident_report && (i.incident_report.status === "draft" || i.incident_report.status === "overdue"));

  // 告警串：Incident ＋ 重要事件
  const feed = useMemo(() => {
    const items: { key: string; ts: string; node: React.ReactNode; hot?: boolean }[] = [];
    for (const inc of (incidents.data ?? []).slice(0, 25)) {
      const wo = woByIncident.get(inc.id);
      items.push({
        key: inc.id,
        ts: inc.opened_at,
        hot: inc.status === "open",
        node: <IncidentRow inc={inc} wo={wo} />,
      });
    }
    for (const ev of events.filter((e) => FEED_TYPES.has(e.type) && e.type !== "incident_opened").slice(0, 30)) {
      items.push({ key: ev.event_id, ts: ev.ts, node: <EventRow ev={ev} /> });
    }
    return items.sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 40);
  }, [incidents.data, events, woByIncident]);

  return (
    <div className="space-y-3">
      {reportIncidents.map((inc) => {
        const wo = woByIncident.get(inc.id);
        return <ReportAlertBar key={inc.id} inc={inc} href={wo ? `/workorders/${wo.id}` : undefined} />;
      })}
      {/* KPI */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Kpi icon={<AlertTriangle className="h-4 w-4" />} label="未結案異常" value={openIncidents.length} tone={openIncidents.length ? "alarm" : "ok"} />
        <Kpi icon={<Radio className="h-4 w-4" />} label="進行中工單" value={m?.open_workorders ?? "—"} />
        <Kpi icon={<BellRing className="h-4 w-4" />} label="規則命中 → 通知" value={m ? `${m.reaction_s.notify} 秒` : "—"} />
        <Kpi icon={<Cpu className="h-4 w-4" />} label="今日 AI 成本（usage.cost）" value={m ? `$${m.ai_cost_usd.toFixed(4)}` : "—"} />
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-3">
          <Card>
            <CardHeader title="攝影機牆" icon={<Video className="h-4 w-4" />} right={
                <div className="flex items-center gap-2">
                  <span className="hidden text-xs text-muted lg:inline">青框 = Jev Box・紅框 = YOLO／規則</span>
                  {!expanded && pages > 1 && (
                    <div className="flex gap-1">
                      {Array.from({ length: pages }, (_, i) => {
                        const camsHere = cameras.slice(i * 4, i * 4 + 4);
                        const hot = camsHere.some((c) => openIncidents.some((x) => x.camera_id === c.id));
                        return (
                          <button
                            key={i}
                            onClick={() => setPage(i)}
                            className={cn("rounded border px-2 py-0.5 text-xs", i === pg ? "border-cyan-500 bg-cyan-500/15 text-cyan-200" : "border-line text-muted hover:text-foreground", hot && i !== pg && "border-red-500 text-red-300")}
                          >
                            {camsHere.map((c) => c.id.replace("CAM-", "")).join("・")}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              }
            />
            <CardContent>
              {plant.error && <ErrorNote e={plant.error} />}
              <div className={cn("grid gap-2", expanded ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2")}>
                {shown.map((c) => {
                  const inc = openIncidents.find((i) => i.camera_id === c.id);
                  return (
                    <CameraTile
                      key={c.id}
                      camera={c}
                      machineName={c.machineName}
                      overlays={overlaysFor(c)}
                      alert={inc ? `${inc.final_severity} ${inc.title ?? inc.type}` : null}
                      expanded={expanded === c.id}
                      onToggle={() => setExpanded((x) => (x === c.id ? null : c.id))}
                    />
                  );
                })}
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-3 md:grid-cols-3">
            {(plant.data?.lines ?? []).map((l) => {
              const avg = l.machines.reduce((a, mm) => a + (mm.utilization ?? 0), 0) / Math.max(1, l.machines.length);
              const lineOpen = openIncidents.filter((i) => i.line === l.id).length;
              return (
                <Card key={l.id}>
                  <CardHeader
                    title={l.name}
                    icon={<Factory className="h-4 w-4" />}
                    right={lineOpen ? <span className="text-xs font-semibold text-red-400">{lineOpen} 件異常</span> : <span className="text-xs text-emerald-400">正常</span>}
                  />
                  <CardContent className="space-y-2.5">
                    {l.machines.length ? (
                      <div className="flex items-center gap-2 text-sm">
                        <Gauge className="h-4 w-4 text-muted" />
                        <span className="text-muted">稼動率</span>
                        <div className="h-2 flex-1 overflow-hidden rounded bg-panel-2">
                          <div className="h-full rounded bg-[#3987e5]" style={{ width: pct(avg) }} />
                        </div>
                        <span className="tabular font-mono text-foreground">{pct(avg)}</span>
                      </div>
                    ) : (
                      <div className="rounded border border-dashed border-line px-3 py-4 text-center text-sm text-muted">
                        尚無設備資料
                        {l.cameras.length > 0 && <div className="mt-0.5 text-xs">攝影機 {l.cameras.map((c) => c.id).join("、")} 監看中</div>}
                      </div>
                    )}
                    {l.machines.map((mm) => (
                      <div key={mm.id} className="flex items-center gap-2 rounded border border-line/60 bg-panel-2/50 px-2 py-1.5">
                        <HealthDot h={mm.health} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm">{mm.name}</div>
                          <div className="font-mono text-[11px] text-muted">{mm.id}</div>
                        </div>
                        <div className="text-right">
                          <KeySignal p={latest[mm.id]} />
                          <div className="text-[11px] text-muted">
                            {mm.health === "alarm" ? <span className="text-red-400">告警</span> : mm.health === "warn" ? <span className="text-amber-300">注意</span> : "運轉中"}・{pct(mm.utilization ?? 0)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>

        <div className="space-y-3">
          <Card className="flex max-h-[640px] flex-col">
            <CardHeader title="即時告警" icon={<BellRing className="h-4 w-4" />} right={<span className="text-xs text-muted">{feed.length} 筆</span>} />
            <div className="min-h-0 flex-1 divide-y divide-line/60 overflow-y-auto">
              {incidents.error && <ErrorNote e={incidents.error} />}
              {feed.map((f) => (
                <div key={f.key} className={cn("flash-in px-3 py-2", f.hot && "bg-red-500/[0.04]")}>
                  {f.node}
                </div>
              ))}
              {!feed.length && <div className="p-4 text-center text-sm text-muted">尚無告警</div>}
            </div>
          </Card>
          <Card>
            <CardHeader title="嚴重度分佈（今日）" />
            <CardContent>
              <SeverityBars counts={sevCounts} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader title="規則 vs AI 處理比例" />
            <CardContent>{m ? <HandledRatio handled={m.handled} /> : <div className="h-16" />}</CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Kpi({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: React.ReactNode; tone?: "ok" | "alarm" }) {
  return (
    <Card className="px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-xs text-muted">
        <span className="text-accent">{icon}</span>
        {label}
      </div>
      <div className={cn("tabular mt-1 font-mono text-2xl font-semibold", tone === "alarm" ? "text-red-400" : tone === "ok" ? "text-emerald-400" : "text-foreground")}>{value}</div>
    </Card>
  );
}

function IncidentRow({ inc, wo }: { inc: Incident; wo?: WorkOrder }) {
  const body = (
    <div className="flex items-start gap-2">
      <SeverityBadge s={inc.final_severity} short className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{inc.title ?? inc.type}</span>
          {inc.status === "open" && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-red-500" />}
        </div>
        <div className="truncate text-xs text-muted">
          {CATEGORY_LABEL[inc.category] ?? inc.category}・{inc.machine_id}・{inc.rule_id}
          {wo && <> ・{wo.id} {STATUS_LABEL[wo.status]}</>}
        </div>
      </div>
      <span className="tabular shrink-0 font-mono text-xs text-muted">{fmtTime(inc.opened_at)}</span>
    </div>
  );
  return wo ? (
    <Link href={`/workorders/${wo.id}`} className="block hover:opacity-80">
      {body}
    </Link>
  ) : (
    body
  );
}

function EventRow({ ev }: { ev: SentinelEvent }) {
  const p = ev.payload as Record<string, unknown>;
  let text = EVENT_TYPE_LABEL[ev.type] ?? ev.type;
  if (ev.type === "jev_vision") {
    const parts = Object.entries(p).filter(([k]) => k.startsWith("p_")).map(([k, v]) => `${k}=${v}`);
    text = `Jev 看圖：${parts.join(" ")}`;
  } else if (ev.type === "ppe_violation") text = `YOLO：${Array.isArray(p.missing) && p.missing.length ? "未戴安全帽" : "人員進入危險區"}（conf ${p.conf}）`;
  else if (ev.type === "machine_alarm") text = `機台警報 ${p.alarm_code}，送料速率 ${p.feed_rate}`;
  else if (ev.type === "machine_state") text = `機台狀態 → ${p.state}（動態 ${p.motion_score}）`;
  else if (ev.type === "scenario_started") text = `▶ ${p.title}`;
  else if (ev.type === "camera_source") text = `切換影片：${String(p.video).split("/").pop()}`;
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="mt-0.5 rounded bg-panel-2 px-1 font-mono text-[11px] text-muted">{ev.source}</span>
      <span className="min-w-0 flex-1 text-slate-300">
        {text}
        <span className="ml-1 font-mono text-[11px] text-muted">{ev.camera_id ?? ev.machine_id}</span>
      </span>
      <span className="tabular shrink-0 font-mono text-xs text-muted">{fmtTime(ev.ts)}</span>
    </div>
  );
}
