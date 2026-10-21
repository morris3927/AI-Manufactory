"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useAsync, useThrottled } from "@/lib/hooks";
import type { AgentMessage, SafetyFlag, WorkOrderDetail } from "@/lib/types";
import { useWs } from "@/lib/ws";
import { toast } from "../toast";

export interface Streaming {
  message_id: string;
  text: string;
}

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
      setStreaming({ message_id: String(d.message_id), text: String(d.text ?? "") });
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
      const flags = d.flags as SafetyFlag[];
      setData((w) =>
        w ? { ...w, agent_messages: (w.agent_messages ?? []).map((x) => (String(x.id) === String(d.message_id) ? { ...x, safety_flags: flags } : x)) } : w,
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
