"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useAsync, useThrottled } from "@/lib/hooks";
import type { AgentGate, AgentMessage, SafetyFlag, WorkOrderDetail } from "@/lib/types";
import { useWs } from "@/lib/ws";
import { toast } from "../toast";

export interface Streaming {
  message_id: string;
  /** 目前為止的全文（後端已逐行過復電過濾） */
  text: string;
  /** 串流期間先到的 J4 結果（safety_gate done=false） */
  flags?: SafetyFlag[];
  gate?: AgentGate | null;
}

type GateUpdate = { flags: SafetyFlag[]; gate: AgentGate };

/** 工單詳情＋即時更新（workorders、ai、agent:{id} 三個頻道） */
export function useWorkorder(id: string) {
  const q = useAsync(() => api.workorder(id), [id]);
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = useThrottled(q.reload, 200);
  const { setData } = q;

  useWs(["workorders", "ai", `agent:${id}`], (m) => {
    const d = (m.data ?? {}) as Record<string, unknown>;
    if (m.channel === "workorders") {
      const wid = (d.workorder as { id?: string } | undefined)?.id ?? d.id;
      if (wid === id || m.type === "reset") reload();
      return;
    }
    if (m.channel === "ai") {
      if (d.workorder_id !== id && d.incident_id !== q.data?.incident_id) return;
      if (m.type === "llm_delta") {
        setData((w) =>
          w && w.diagnosis ? { ...w, diagnosis: { ...w.diagnosis, explanation: String(d.text ?? ""), explanation_status: "streaming" } } : w,
        );
      } else reload();
      return;
    }
    // agent:{id}
    if (m.type === "delta") {
      const mid = String(d.message_id);
      setStreaming((s) => ({ message_id: mid, text: String(d.text ?? ""), flags: s?.message_id === mid ? s.flags : undefined, gate: s?.message_id === mid ? s.gate : { status: "pending", checked: [] } }));
    } else if (m.type === "message") {
      const msg = m.data as AgentMessage;
      setData((w) => {
        if (!w) return w;
        const list = w.agent_messages ?? [];
        if (list.some((x) => String(x.id) === String(msg.id))) return { ...w, agent_messages: list.map((x) => (String(x.id) === String(msg.id) ? msg : x)) };
        return { ...w, agent_messages: [...list, msg] };
      });
      setStreaming((s) => (s && String(s.message_id) === String(msg.id) ? null : s));
    } else if (m.type === "safety_gate") {
      const mid = String(d.message_id);
      // done 缺省（舊格式、主管確認）視為把關完成
      const up: GateUpdate = {
        flags: (d.flags as SafetyFlag[]) ?? [],
        gate: (d.gate as AgentGate | undefined) ?? { status: d.done === false ? "pending" : "done" },
      };
      setStreaming((s) => (s && s.message_id === mid ? { ...s, ...up } : s));
      setData((w) =>
        w ? { ...w, agent_messages: (w.agent_messages ?? []).map((x) => (String(x.id) === mid ? { ...x, safety_flags: up.flags, gate: up.gate } : x)) } : w,
      );
    }
  });

  /** 執行操作：成功時若回傳工單就直接更新，否則重抓 */
  const act = useCallback(
    async <T,>(fn: () => Promise<T>, okText?: string): Promise<T | undefined> => {
      setBusy(true);
      try {
        const r = await fn();
        const maybe = r as unknown as Partial<WorkOrderDetail> & { workorder?: WorkOrderDetail };
        if (maybe && typeof maybe === "object" && "status" in maybe && "id" in maybe) setData(() => maybe as WorkOrderDetail);
        else if (maybe?.workorder) setData(() => maybe.workorder);
        else reload();
        if (okText) toast(okText, "ok");
        return r;
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e), "error");
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [reload, setData],
  );

  return { wo: q.data, error: q.error, loading: q.loading, reload: q.reload, streaming, act, busy };
}
