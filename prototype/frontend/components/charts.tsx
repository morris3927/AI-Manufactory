"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ComposedChart,
  Tooltip,
  XAxis,
  YAxis,
  LabelList,
} from "recharts";
import { SEVERITY_STYLE } from "@/lib/labels";
import type { CalibrationBin, RootCause, Severity, SignalPoint } from "@/lib/types";
import { fmtTime } from "@/lib/utils";

// 已用 dataviz validator 驗證（深色底）：藍／橘／綠
export const SERIES = ["#3987e5", "#d95926", "#199e70"];
const AXIS = { stroke: "#334155", tick: { fill: "#95a9bd", fontSize: 12 } };
const GRID = <CartesianGrid stroke="#1e2b39" strokeDasharray="0" vertical={false} />;
const TIP = {
  contentStyle: { background: "#0e151d", border: "1px solid #1e2b39", borderRadius: 6, fontSize: 12, color: "#dbe5ef" },
  labelStyle: { color: "#7f93a8" },
  itemStyle: { color: "#dbe5ef" },
  cursor: { fill: "rgba(148,163,184,0.08)", stroke: "#475569" },
};

/** 根因前三名機率長條圖（水平） */
export function RootCauseBars({ items, height = 150 }: { items: RootCause[]; height?: number }) {
  const data = items.map((r, i) => ({ name: `${i + 1}. ${r.name}`, p: Math.round(r.p * 100), id: r.failure_mode_id }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 44, bottom: 4, left: 4 }} barCategoryGap={10}>
        <XAxis type="number" domain={[0, 100]} hide />
        <YAxis type="category" dataKey="name" width={118} {...AXIS} tick={{ fill: "#dbe5ef", fontSize: 12 }} axisLine={false} tickLine={false} />
        <Tooltip {...TIP} formatter={(v) => [`${v}%`, "機率"]} />
        <Bar dataKey="p" radius={[0, 4, 4, 0]} isAnimationActive={false}>
          {data.map((_, i) => (
            <Cell key={i} fill="#3987e5" fillOpacity={i === 0 ? 1 : i === 1 ? 0.7 : 0.45} />
          ))}
          <LabelList dataKey="p" position="right" formatter={(v) => `${v}%`} fill="#dbe5ef" fontSize={12} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const SIGNAL_META: Record<string, { label: string; unit: string }> = {
  feed_rate: { label: "送料速率", unit: "m/min" },
  feed_length_error_mm: { label: "送料長度誤差", unit: "mm" },
  motor_current_a: { label: "馬達電流", unit: "A" },
  motor_temp_c: { label: "馬達溫度", unit: "°C" },
  spm: { label: "沖程數", unit: "spm" },
  tonnage_peak: { label: "峰值噸數", unit: "t" },
  motion_score: { label: "影像動態分數", unit: "" },
};

/** 訊號圖：每個訊號一張小圖（不同單位不共用 y 軸） */
export function SignalCharts({ points, signals, marker }: { points: SignalPoint[]; signals: string[]; marker?: string | null }) {
  return (
    <div className="space-y-2">
      {signals.map((sig) => {
        const meta = SIGNAL_META[sig] ?? { label: sig, unit: "" };
        const last = points.at(-1)?.[sig];
        return (
          <div key={sig}>
            <div className="mb-0.5 flex items-baseline justify-between text-xs">
              <span className="text-muted">{meta.label}</span>
              <span className="tabular font-mono text-foreground">
                {typeof last === "number" ? last : "—"} <span className="text-muted">{meta.unit}</span>
              </span>
            </div>
            <ResponsiveContainer width="100%" height={70}>
              <LineChart data={points} margin={{ top: 2, right: 4, bottom: 0, left: 0 }}>
                {GRID}
                <XAxis dataKey="ts" hide />
                <YAxis width={36} {...AXIS} axisLine={false} tickLine={false} domain={["auto", "auto"]} tickCount={3} />
                <Tooltip {...TIP} labelFormatter={(l) => fmtTime(String(l))} formatter={(v) => [`${v} ${meta.unit}`, meta.label]} />
                {marker && <ReferenceLine x={marker} stroke="#ef4444" strokeDasharray="3 3" />}
                <Line type="monotone" dataKey={sig} stroke="#3987e5" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}

export function SeverityBars({ counts }: { counts: Partial<Record<Severity, number>> }) {
  const data = (["S1", "S2", "S3"] as Severity[]).map((s) => ({ s, n: counts[s] ?? 0 }));
  return (
    <ResponsiveContainer width="100%" height={130}>
      <BarChart data={data} margin={{ top: 16, right: 8, bottom: 0, left: -18 }} barCategoryGap="30%">
        {GRID}
        <XAxis dataKey="s" {...AXIS} tickLine={false} />
        <YAxis allowDecimals={false} {...AXIS} axisLine={false} tickLine={false} />
        <Tooltip {...TIP} formatter={(v) => [v, "件數"]} />
        <Bar dataKey="n" radius={[4, 4, 0, 0]} isAnimationActive={false}>
          {data.map((d) => (
            <Cell key={d.s} fill={SEVERITY_STYLE[d.s].hex} />
          ))}
          <LabelList dataKey="n" position="top" fill="#dbe5ef" fontSize={11} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** 規則 vs AI 處理比例：100% 堆疊條＋圖例（含數字） */
export function HandledRatio({ handled }: { handled: { rules_only: number; jev: number; llm: number } }) {
  const parts = [
    { k: "rules_only", label: "只用規則", n: handled.rules_only, c: SERIES[0] },
    { k: "jev", label: "Jev 判斷", n: handled.jev, c: SERIES[1] },
    { k: "llm", label: "LLM 深度說明", n: handled.llm, c: SERIES[2] },
  ];
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  return (
    <div>
      <div className="flex h-4 w-full gap-[2px] overflow-hidden rounded">
        {parts.map((p) => (
          <div key={p.k} style={{ width: `${(p.n / total) * 100}%`, background: p.c }} title={`${p.label}：${p.n}`} />
        ))}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
        {parts.map((p) => (
          <div key={p.k} className="flex items-start gap-1.5">
            <span className="mt-1 h-2 w-2 shrink-0 rounded-sm" style={{ background: p.c }} />
            <div>
              <div className="text-muted">{p.label}</div>
              <div className="tabular font-mono text-foreground">
                {p.n} <span className="text-muted">({Math.round((p.n / total) * 100)}%)</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function CostLine({ series }: { series: { ts: string; cost: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        {GRID}
        <XAxis dataKey="ts" {...AXIS} tickFormatter={(t) => fmtTime(String(t), false)} minTickGap={40} />
        <YAxis width={56} {...AXIS} axisLine={false} tickLine={false} tickFormatter={(v) => `$${Number(v).toFixed(3)}`} />
        <Tooltip {...TIP} labelFormatter={(l) => fmtTime(String(l))} formatter={(v) => [`$${Number(v).toFixed(4)}`, "累計成本"]} />
        <Line type="stepAfter" dataKey="cost" stroke={SERIES[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  const data = bins.map((b) => ({ x: b.p_pred, y: b.p_obs, n: b.n }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 18, left: 0 }}>
        <CartesianGrid stroke="#1e2b39" />
        <XAxis type="number" dataKey="x" domain={[0, 1]} ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]} {...AXIS} label={{ value: "預測機率", position: "insideBottom", offset: -10, fill: "#95a9bd", fontSize: 12 }} />
        <YAxis type="number" domain={[0, 1]} ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]} width={36} {...AXIS} />
        <Tooltip {...TIP} formatter={(v, name) => [Number(v).toFixed(2), name === "y" ? "實際發生率" : String(name)]} labelFormatter={(l) => `預測 ${Number(l).toFixed(2)}`} />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#475569" strokeDasharray="4 4" />
        <Line type="linear" dataKey="y" stroke={SERIES[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
        <Scatter dataKey="y" fill={SERIES[0]} stroke="#0e151d" strokeWidth={2} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
