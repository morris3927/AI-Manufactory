"use client";

import { AlertTriangle, Loader2, Lock, LockOpen, ShieldAlert, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { CLOSING_REMINDER } from "@/lib/labels";
import { usePersona } from "@/lib/session";
import type { AgentMessage, SafetyFlag, WorkOrderDetail } from "@/lib/types";
import { cn, fmtTime } from "@/lib/utils";
import { Button } from "../ui/button";

type Act = <T>(fn: () => Promise<T>, okText?: string) => Promise<T | undefined>;

const PROB_LABEL: Record<string, string> = {
  needs_loto: "需上鎖掛牌",
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

/** 後端 config.yaml thresholds.gate_supervisor：進入危險區機率 ≥ 此值才需主管確認（gate_set 標為危險的手入模區步驟最低 0.73） */
export const GATE_SUPERVISOR_THRESHOLDS: Record<string, number> = { enters_danger_zone: 0.7 };

/** 「注意」級的提醒文字，依觸發的題目決定（給現場人員看，不寫術語） */
const CAUTION_TEXT: Record<string, string> = {
  enters_danger_zone: "注意：可能靠近危險區，作業時保持距離",
  needs_loto: "注意：作業前確認已上鎖掛牌、機台不會動",
  high_voltage: "注意：可能碰到電氣部位，先確認已斷電",
  energized: "注意：這步可能讓機台動作，先確認周圍沒人",
};

/** 旗標等級：caution 只提醒、lockout 只缺上鎖掛牌；舊資料（沒有 level）一律當成需主管確認 */
export function flagLevel(f: SafetyFlag): "lockout" | "caution" | "supervisor" {
  return f.level === "caution" || f.level === "lockout" ? f.level : "supervisor";
}

const LOCKOUT_TEXT = "需先完成上鎖掛牌：這一步要碰機台，請先申請作業許可、完成上鎖掛牌再做";

/** 待主管確認的旗標（只算 supervisor 級、且還沒確認） */
export function pendingSupervisor(flags: SafetyFlag[]): SafetyFlag[] {
  return flags.filter((f) => flagLevel(f) === "supervisor" && f.supervisor_requested);
}

function cautionText(f: SafetyFlag): string {
  const keys = f.reasons?.length ? f.reasons : Object.entries(f.probs ?? {}).sort((a, b) => b[1] - a[1]).map(([k]) => k);
  return CAUTION_TEXT[keys.find((k) => CAUTION_TEXT[k]) ?? "enters_danger_zone"];
}

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
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i} className="font-semibold text-foreground">{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded bg-background/70 px-1 font-mono text-[0.9em]">{part.slice(1, -1)}</code>;
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
  const level = flagLevel(f);
  const lockout = level === "lockout" || !!f.needs_lockout;
  const shown = entries.filter(([k, v]) => over(k, v) && !(k === "needs_loto" && lockout));
  const sup = level === "supervisor";
  if (level === "lockout")
    return (
      <div className="mt-1 flex items-center gap-1 text-[13px] text-orange-300">
        <Lock className="h-3.5 w-3.5 shrink-0" /> {LOCKOUT_TEXT}
      </div>
    );
  const thTitle = (k: string) =>
    `門檻 ${GATE_THRESHOLDS[k] ?? 0.3}` + (GATE_SUPERVISOR_THRESHOLDS[k] !== undefined ? `；≥ ${GATE_SUPERVISOR_THRESHOLDS[k]} 需主管確認` : "");
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1 text-xs">
      {sup ? <ShieldAlert className="h-3.5 w-3.5 text-red-300" /> : <AlertTriangle className="h-3.5 w-3.5 text-amber-300" />}
      <span className={sup ? "text-red-300" : "text-amber-300"}>
        {sup ? "需主管確認" : "注意"}・Jev 安全把關{f.source && f.source !== "jev" ? `（${f.source}）` : ""}
      </span>
      {(shown.length ? shown : entries.slice(0, 1)).map(([k, v]) => (
        <span
          key={k}
          title={thTitle(k)}
          className={cn("rounded px-1.5 py-px font-mono", sup ? "bg-red-500/25 text-red-100" : "bg-amber-500/20 text-amber-100")}
        >
          {PROB_LABEL[k] ?? k} {v.toFixed(2)}
        </span>
      ))}
      {!sup ? null : f.supervisor_requested ? (
        <span className="text-red-300">・待主管確認</span>
      ) : f.acked_by ? (
        <span className="flex items-center gap-0.5 text-emerald-300">
          <ShieldCheck className="h-3.5 w-3.5" /> 主管已確認（{f.acked_by}）
        </span>
      ) : null}
      {!sup && <div className="basis-full text-[13px] text-amber-200">{cautionText(f)}</div>}
      {lockout && (
        <div className="flex basis-full items-center gap-1 text-[13px] text-orange-300">
          <Lock className="h-3.5 w-3.5 shrink-0" /> {LOCKOUT_TEXT}
        </div>
      )}
    </div>
  );
}

type GateState = "pending" | "cleared" | undefined;

function Step({ no, text, flag, gate }: { no?: string; text: string; flag?: SafetyFlag; gate?: GateState }) {
  // 需主管確認＝紅；注意＝黃（不需確認）；只缺上鎖掛牌＝橘（不需主管）
  const level = flag ? flagLevel(flag) : undefined;
  const tone =
    level === "supervisor"
      ? "border-red-500/70 bg-red-500/10"
      : level === "caution"
        ? "border-amber-400/70 bg-amber-400/10"
        : level === "lockout"
          ? "border-orange-500/70 bg-orange-500/10"
          : "border-transparent";
  return (
    <li className={cn("flex gap-2 rounded border-l-4 px-2 py-1", tone)} data-gate-level={level ?? "none"}>
      {no !== undefined && (
        <span className={cn("shrink-0 font-mono text-sm", level === "supervisor" ? "text-red-200" : level === "caution" ? "text-amber-200" : level === "lockout" ? "text-orange-200" : "text-cyan-300")}>{no}.</span>
      )}
      {no === undefined && <span className="shrink-0 text-cyan-300">•</span>}
      <div className="min-w-0 flex-1">
        <span className="leading-relaxed">{inline(text)}</span>
        {flag && <FlagChips f={flag} />}
        {!flag && gate === "pending" && (
          <div className="mt-0.5 flex items-center gap-1 text-xs text-amber-200/80">
            <Loader2 className="h-3 w-3 animate-spin" /> 安全把關檢查中…
          </div>
        )}
        {!flag && gate === "cleared" && (
          <span className="ml-1 inline-flex items-center align-middle text-emerald-400/70" title="Jev 安全把關：未超過門檻">
            <ShieldCheck className="h-3.5 w-3.5" />
          </span>
        )}
      </div>
    </li>
  );
}

/** 步驟的把關狀態：沒有 gate 欄位（舊訊息、LOTO 模板）→ 不顯示；pending 且尚未檢查 → 檢查中 */
function gateState(m: AgentMessage, text: string): GateState {
  const g = m.gate;
  if (!g) return undefined;
  if (g.status === "done" || (g.checked ?? []).some((c) => similar(text, c))) return "cleared";
  return "pending";
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
  // 結尾的復機提醒不是步驟：整行相同才抽出來，最後用小字淺底另外畫
  const lines = (m.content ?? "").split("\n");
  const remind = lines.some((l) => l.trim() === CLOSING_REMINDER) || (m.steps ?? []).some((s) => s.trim() === CLOSING_REMINDER);
  const steps = (m.steps ?? []).filter((s) => s.trim() !== CLOSING_REMINDER);
  const blocks = parse(lines.filter((l) => l.trim() !== CLOSING_REMINDER).join("\n"));
  const used = new Set<SafetyFlag>();
  const hasList = blocks.some((b) => b.kind !== "p");
  const out = blocks.map((b, i) => {
    if (b.kind === "p")
      return (
        <p key={i} className="whitespace-pre-wrap leading-relaxed text-foreground">
          {inline(b.text)}
        </p>
      );
    const Tag = b.kind === "ol" ? "ol" : "ul";
    return (
      <Tag key={i} className="space-y-1">
        {b.items.map((it, j) => {
          const f = matchFlag(it.text, steps, flags);
          if (f) used.add(f);
          return <Step key={j} no={it.no} text={it.text} flag={f} gate={gateState(m, it.text)} />;
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
          return <Step key={i} no={String(i + 1)} text={s} flag={f} gate={gateState(m, s)} />;
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
  if (remind) {
    out.push(
      <div key="remind" className="flex items-start gap-1.5 rounded-md border border-line bg-background/60 px-2.5 py-1.5 text-[13px] leading-relaxed text-muted">
        <LockOpen className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{CLOSING_REMINDER}</span>
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
  const gating = m.gate?.status === "pending";
  // 把關還在跑時先不給「主管確認」：最終旗標以全部步驟檢查完的結果為準
  // 只有「需主管確認」級才出現確認區塊；只有「注意」級的回覆不用任何人按
  const waiting = pendingSupervisor(flags);
  const pending = !gating && waiting.length > 0;
  const isSup = persona.role === "supervisor";
  return (
    <div className="max-w-[96%] rounded-lg rounded-tl-none border border-line bg-panel-2 px-3 py-2">
      <AssistantContent m={m} />
      {gating && (
        <div className="mt-1.5 flex items-center gap-1 text-xs text-amber-200/80">
          <Loader2 className="h-3 w-3 animate-spin" /> Jev 安全把關檢查中…（需主管確認的步驟會自動通知主管）
        </div>
      )}
      {pending && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-red-500/40 bg-red-500/5 px-2 py-1.5">
          <ShieldAlert className="h-4 w-4 text-red-300" />
          <span className="text-sm text-red-200">有 {waiting.length} 個步驟待主管確認</span>
          <Button
            size="sm"
            variant="warn"
            className="ml-auto"
            disabled={!isSup}
            title={isSup ? undefined : "需主管身分（右上角切換）"}
            onClick={() =>
              act(
                () => api.override({ target: "workorder", target_id: wo.id, field: "safety_ack", new: String(m.id), reason: "主管已確認安全措施：禁止徒手進入危險區，依上鎖掛牌與 SOP 執行" }),
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
            <span key={i} className="rounded bg-background/60 px-1.5 py-0.5 font-mono text-xs text-muted" title={JSON.stringify(t.args ?? {})}>
              ƒ {t.name}
            </span>
          ))}
        </div>
      )}
      <div className="mt-0.5 font-mono text-xs text-muted">{fmtTime(m.ts)}</div>
    </div>
  );
}
