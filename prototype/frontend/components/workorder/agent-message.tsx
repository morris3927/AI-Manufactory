"use client";

import { ShieldAlert, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { usePersona } from "@/lib/session";
import type { AgentMessage, SafetyFlag, WorkOrderDetail } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";
import { Button } from "../ui/button";

type Act = <T>(fn: () => Promise<T>, okText?: string) => Promise<T | undefined>;

const PROB_LABEL: Record<string, string> = {
  needs_loto: "需 LOTO",
  enters_danger_zone: "進入危險區",
  high_voltage: "高壓電",
  bypasses_guard: "繞過防護",
  energized: "帶電作業",
};

/** 後端 config.yaml thresholds.gate（2026-10-21 以 gate_set 校準）；flag 帶 reasons 時以後端為準 */
export const GATE_THRESHOLDS: Record<string, number> = {
  needs_loto: 0.3,
  enters_danger_zone: 0.55,
  high_voltage: 0.45,
  energized: 0.55,
};

const NUM_RE = /^\s*(\d+)[.、)）]\s*/;
const BULLET_RE = /^\s*[-•*・]\s+/;

/** 去掉編號與空白，用來比對步驟文字 */
function norm(s: string) {
  return s.replace(NUM_RE, "").replace(/[\s*`]/g, "");
}

function similar(a: string, b: string) {
  const x = norm(a);
  const y = norm(b);
  if (x.length < 6 || y.length < 6) return x === y && x.length > 0;
  const n = Math.min(20, x.length, y.length);
  return x.includes(y.slice(0, n)) || y.includes(x.slice(0, n));
}

function matchFlag(text: string, steps: string[], flags: SafetyFlag[]): SafetyFlag | undefined {
  const byText = flags.find((f) => f.step_text && similar(text, f.step_text));
  if (byText) return byText;
  const si = steps.findIndex((s) => similar(text, s));
  return si >= 0 ? flags.find((f) => f.step_index === si) : undefined;
}

/** **粗體** 與 `程式碼` 的簡易行內格式 */
function inline(s: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i} className="font-semibold text-white">{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded bg-black/40 px-1 font-mono text-[0.9em]">{part.slice(1, -1)}</code>;
    return part;
  });
}

type Block = { kind: "p"; text: string } | { kind: "ol" | "ul"; items: { no?: string; text: string }[] };

function parse(content: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join("\n") });
    para = [];
  };
  for (const raw of content.split("\n")) {
    const line = raw.trimEnd();
    const num = line.match(NUM_RE);
    const bullet = !num && BULLET_RE.test(line);
    const last = blocks.at(-1);
    if (num || bullet) {
      flush();
      const kind = num ? "ol" : "ul";
      const item = { no: num?.[1], text: line.replace(num ? NUM_RE : BULLET_RE, "") };
      if (last && last.kind === kind && para.length === 0) last.items.push(item);
      else blocks.push({ kind, items: [item] });
    } else if (!line.trim()) {
      flush();
    } else if (last && last.kind !== "p" && para.length === 0 && /^\s{2,}/.test(raw)) {
      last.items[last.items.length - 1].text += " " + line.trim(); // 縮排的續行
    } else para.push(line);
  }
  flush();
  return blocks;
}

function FlagChips({ f }: { f: SafetyFlag }) {
  const entries = Object.entries(f.probs ?? {}).sort((a, b) => b[1] - a[1]);
  const over = (k: string, v: number) => (f.reasons ? f.reasons.includes(k) : v >= (GATE_THRESHOLDS[k] ?? 0.3));
  const shown = entries.filter(([k, v]) => over(k, v));
  const red = f.max_p >= 0.6;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1 text-xs">
      <ShieldAlert className={cn("h-3.5 w-3.5", red ? "text-red-300" : "text-amber-300")} />
      <span className={red ? "text-red-300" : "text-amber-300"}>Jev 安全把關{f.source && f.source !== "jev" ? `（${f.source}）` : ""}</span>
      {(shown.length ? shown : entries.slice(0, 1)).map(([k, v]) => (
        <span
          key={k}
          title={`門檻 ${GATE_THRESHOLDS[k] ?? 0.3}`}
          className={cn("rounded px-1.5 py-px font-mono", v >= 0.6 ? "bg-red-500/25 text-red-100" : "bg-amber-500/20 text-amber-100")}
        >
          {PROB_LABEL[k] ?? k} {v.toFixed(2)}
        </span>
      ))}
      {f.supervisor_requested ? (
        <span className="text-red-300">・待主管確認</span>
      ) : f.acked_by ? (
        <span className="flex items-center gap-0.5 text-emerald-300">
          <ShieldCheck className="h-3.5 w-3.5" /> 主管已確認（{f.acked_by}）
        </span>
      ) : null}
    </div>
  );
}

function Step({ no, text, flag }: { no?: string; text: string; flag?: SafetyFlag }) {
  const tone = flag ? (flag.max_p >= 0.6 ? "border-red-500/70 bg-red-500/10" : "border-amber-500/60 bg-amber-500/10") : "border-transparent";
  return (
    <li className={cn("flex gap-2 rounded border-l-4 px-2 py-1", tone)}>
      {no !== undefined && <span className={cn("shrink-0 font-mono text-sm", flag ? "text-red-200" : "text-cyan-300")}>{no}.</span>}
      {no === undefined && <span className="shrink-0 text-cyan-300">•</span>}
      <div className="min-w-0 flex-1">
        <span className="leading-relaxed">{inline(text)}</span>
        {flag && <FlagChips f={flag} />}
      </div>
    </li>
  );
}

export function sortMessages(list: AgentMessage[]): AgentMessage[] {
  return [...list].sort((a, b) => {
    const t = (a.ts ?? "").localeCompare(b.ts ?? "");
    if (t) return t;
    const na = Number(a.id);
    const nb = Number(b.id);
    return Number.isFinite(na) && Number.isFinite(nb) ? na - nb : String(a.id).localeCompare(String(b.id));
  });
}

export function AssistantContent({ m }: { m: AgentMessage }) {
  const flags = m.safety_flags ?? [];
  const steps = m.steps ?? [];
  const blocks = parse(m.content ?? "");
  const used = new Set<SafetyFlag>();
  const hasList = blocks.some((b) => b.kind !== "p");
  const out = blocks.map((b, i) => {
    if (b.kind === "p")
      return (
        <p key={i} className="whitespace-pre-wrap leading-relaxed text-slate-100">
          {inline(b.text)}
        </p>
      );
    const Tag = b.kind === "ol" ? "ol" : "ul";
    return (
      <Tag key={i} className="space-y-1">
        {b.items.map((it, j) => {
          const f = matchFlag(it.text, steps, flags);
          if (f) used.add(f);
          return <Step key={j} no={it.no} text={it.text} flag={f} />;
        })}
      </Tag>
    );
  });
  // 內文沒有編號清單，但後端另外給了 steps → 補畫步驟
  if (!hasList && steps.length) {
    out.push(
      <ol key="steps" className="space-y-1">
        {steps.map((s, i) => {
          const f = flags.find((x) => x.step_index === i) ?? matchFlag(s, steps, flags);
          if (f) used.add(f);
          return <Step key={i} no={String(i + 1)} text={s} flag={f} />;
        })}
      </ol>,
    );
  }
  const rest = flags.filter((f) => !used.has(f));
  if (rest.length) {
    out.push(
      <div key="rest" className="space-y-1 border-t border-line/60 pt-2">
        <div className="text-xs text-muted">其他被標記的步驟</div>
        <ul className="space-y-1">
          {rest.map((f, i) => (
            <Step key={i} no={String(f.step_index + 1)} text={f.step_text} flag={f} />
          ))}
        </ul>
      </div>,
    );
  }
  return <div className="space-y-2 text-[15px]">{out}</div>;
}

export function ChatBubble({ m, wo, act }: { m: AgentMessage; wo: WorkOrderDetail; act: Act }) {
  const persona = usePersona();
  if (m.role === "user")
    return (
      <div className="ml-auto max-w-[85%] rounded-lg rounded-tr-none bg-cyan-600/25 px-3 py-2 text-[15px] text-cyan-50">
        <div className="whitespace-pre-wrap">{m.content}</div>
        <div className="mt-0.5 text-right font-mono text-xs text-cyan-200/60">{fmtTime(m.ts)}</div>
      </div>
    );
  if (m.role === "system" || m.role === "tool")
    return (
      <div className={cn("rounded-md border px-3 py-1.5 text-sm", m.content.startsWith("安全把關") ? "border-red-500/60 bg-red-500/10 text-red-200" : "border-line bg-panel-2/60 text-muted")}>
        {m.content.startsWith("安全把關") && <ShieldAlert className="mr-1 inline h-4 w-4" />}
        {m.content}
      </div>
    );
  const flags = m.safety_flags ?? [];
  const pending = flags.some((f) => f.supervisor_requested);
  const isSup = persona.role === "supervisor";
  return (
    <div className="max-w-[96%] rounded-lg rounded-tl-none border border-line bg-panel-2 px-3 py-2">
      <AssistantContent m={m} />
      {pending && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-red-500/40 bg-red-500/5 px-2 py-1.5">
          <ShieldAlert className="h-4 w-4 text-red-300" />
          <span className="text-sm text-red-200">有 {flags.filter((f) => f.supervisor_requested).length} 個步驟待主管確認</span>
          <Button
            size="sm"
            variant="warn"
            className="ml-auto"
            disabled={!isSup}
            title={isSup ? undefined : "需主管身分（右上角切換）"}
            onClick={() =>
              act(
                () => api.override({ target: "workorder", target_id: wo.id, field: "safety_ack", new: String(m.id), reason: "主管已確認安全措施：禁止徒手進入危險區，依 LOTO 與 SOP 執行" }),
                "已確認安全把關",
              )
            }
          >
            {isSup ? "主管確認" : "主管確認（需主管身分）"}
          </Button>
        </div>
      )}
      {!!m.tool_calls?.length && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {m.tool_calls.map((t, i) => (
            <span key={i} className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-xs text-muted" title={JSON.stringify(t.args ?? {})}>
              ƒ {t.name}
            </span>
          ))}
        </div>
      )}
      <div className="mt-0.5 font-mono text-xs text-muted">{fmtTime(m.ts)}</div>
    </div>
  );
}
