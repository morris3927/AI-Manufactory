"use client";

import { useState } from "react";
import { BarChart3, Clock, DollarSign, Target, Wrench } from "lucide-react";
import { CalibrationChart, CostLine, HandledRatio } from "@/components/charts";
import { ErrorNote } from "@/components/error-note";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { MetricsSummary } from "@/lib/types";
import { pct } from "@/lib/utils";
import { useWs } from "@/lib/ws";

function Stat({ label, value, unit, note, icon }: { label: string; value: string; unit?: string; note?: string; icon: React.ReactNode }) {
  return (
    <Card className="p-3">
      <div className="flex items-center gap-1.5 text-xs text-muted">
        <span className="text-accent">{icon}</span>
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="tabular font-mono text-3xl font-semibold">{value}</span>
        {unit && <span className="text-sm text-muted">{unit}</span>}
      </div>
      {note && <div className="mt-0.5 text-[11px] text-muted">{note}</div>}
    </Card>
  );
}

export default function MetricsPage() {
  const summary = useAsync(() => api.metricsSummary(), []);
  const calib = useAsync(() => api.calibration(), []);
  const [live, setLive] = useState<MetricsSummary | null>(null);
  useWs(["metrics"], (m) => setLive(m.data as MetricsSummary));
  const s = live ?? summary.data;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <BarChart3 className="h-5 w-5 text-accent" />
        <h1 className="text-lg font-semibold">效益</h1>
        <span className="text-xs text-muted">每 5 秒更新</span>
      </div>
      {summary.error && <ErrorNote e={summary.error} />}
      {s && (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
            <Stat icon={<Clock className="h-4 w-4" />} label="發現（異常 → 規則命中）" value={s.reaction_s.detect.toFixed(1)} unit="秒" />
            <Stat icon={<Clock className="h-4 w-4" />} label="通知（命中 → 推播）" value={s.reaction_s.notify.toFixed(2)} unit="秒" note="目標 < 1 秒" />
            <Stat icon={<Clock className="h-4 w-4" />} label="到場（推播 → 接單）" value={s.reaction_s.arrive.toFixed(0)} unit="秒" />
            <Stat icon={<Wrench className="h-4 w-4" />} label="MTTR" value={s.mttr_min.toFixed(1)} unit="分鐘" note="設備類，建單到結案" />
            <Stat icon={<DollarSign className="h-4 w-4" />} label="AI 成本累計" value={`$${s.ai_cost_usd.toFixed(4)}`} note="Jev＋LLM 真實 usage.cost 加總" />
            <Stat icon={<Target className="h-4 w-4" />} label="今日異常" value={String(s.incidents_total ?? "—")} unit="件" note={`進行中工單 ${s.open_workorders ?? "—"}`} />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <CardHeader title="AI 成本累計（USD）" icon={<DollarSign className="h-4 w-4" />} />
              <CardContent>{s.ai_cost_series?.length ? <CostLine series={s.ai_cost_series} /> : <div className="text-xs text-muted">無資料</div>}</CardContent>
            </Card>
            <Card>
              <CardHeader
                title="校準曲線（Jev 根因機率）"
                icon={<Target className="h-4 w-4" />}
                right={calib.data && <span className="font-mono text-[11px] text-muted">Brier {calib.data.brier?.toFixed(3)}・ECE {calib.data.ece?.toFixed(3)}・n={calib.data.n_total}</span>}
              />
              <CardContent>
                {calib.data ? <CalibrationChart bins={calib.data.bins} /> : calib.error ? <ErrorNote e={calib.error} /> : null}
                <p className="text-[11px] text-muted">虛線為完美校準；點越貼近虛線，代表「說 70% 的事大約 70% 會發生」。</p>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
            <Card>
              <CardHeader title="規則 vs AI 處理比例" />
              <CardContent>
                <HandledRatio handled={s.handled} />
                <p className="mt-3 text-[11px] text-muted">工安類與明確規則直接派工，不呼叫 AI；只有需要判斷根因時才用 Jev，需要長說明時才用 LLM。</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader title="規則／Jev／LLM 比較" />
              <CardContent className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-muted">
                      <th className="py-1.5 font-normal">方法</th>
                      <th className="py-1.5 text-right font-normal">平均延遲</th>
                      <th className="py-1.5 text-right font-normal">根因命中率（測試集）</th>
                      <th className="py-1.5 text-right font-normal">每次成本</th>
                      <th className="py-1.5 text-right font-normal">今日呼叫</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(s.comparison ?? []).map((c) => (
                      <tr key={c.method} className="border-b border-line/50">
                        <td className="py-2">{c.method}</td>
                        <td className="tabular py-2 text-right font-mono">{c.latency_ms >= 1000 ? `${(c.latency_ms / 1000).toFixed(2)} s` : `${c.latency_ms} ms`}</td>
                        <td className="tabular py-2 text-right font-mono">{c.accuracy === null ? "—" : pct(c.accuracy)}</td>
                        <td className="tabular py-2 text-right font-mono">{c.cost_per_call_usd ? `$${c.cost_per_call_usd.toFixed(5)}` : "免費"}</td>
                        <td className="tabular py-2 text-right font-mono">{c.calls}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
