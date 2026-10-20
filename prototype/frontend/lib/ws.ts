"use client";

// WebSocket client，對應 08_系統設計.md §7.2。
// 訊息格式：{"channel": "...", "type": "...", "data": {...}, "ts": "..."}
// 連線後送出 {"action":"subscribe","channels":[...]}（假設的訂閱指令）；
// 身分以 query string 帶上（瀏覽器 WebSocket 不能自訂 header）。

import { useEffect, useRef, useSyncExternalStore } from "react";
import { MOCK, WS_URL } from "./config";
import { getPersona } from "./session";
import type { WsMessage } from "./types";

type Handler = (msg: WsMessage) => void;
export type WsStatus = "connecting" | "open" | "closed" | "mock";

class WsClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>(); // channel → handlers（"*" = 全部）
  private status: WsStatus = MOCK ? "mock" : "closed";
  private statusListeners = new Set<() => void>();
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private mockUnsub: (() => void) | null = null;

  getStatus = () => this.status;

  onStatus = (l: () => void) => {
    this.statusListeners.add(l);
    return () => {
      this.statusListeners.delete(l);
    };
  };

  private setStatus(s: WsStatus) {
    this.status = s;
    this.statusListeners.forEach((l) => l());
  }

  private channels(): string[] {
    return [...this.handlers.keys()].filter((c) => c !== "*" && (this.handlers.get(c)?.size ?? 0) > 0);
  }

  private dispatch(msg: WsMessage) {
    this.handlers.get(msg.channel)?.forEach((h) => h(msg));
    this.handlers.get("*")?.forEach((h) => h(msg));
  }

  private ensure() {
    if (typeof window === "undefined") return;
    if (MOCK) {
      if (!this.mockUnsub) {
        void import("./mock/server").then(({ mockBus }) => {
          if (!this.mockUnsub) this.mockUnsub = mockBus.subscribe((m) => this.dispatch(m));
        });
      }
      return;
    }
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    const p = getPersona();
    const url = `${WS_URL}?role=${encodeURIComponent(p.role)}&person_id=${encodeURIComponent(p.personId)}`;
    this.setStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setStatus("open");
      this.sendSubscribe(this.channels());
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as WsMessage;
        if (msg && msg.channel) this.dispatch(msg);
      } catch {
        /* 忽略非 JSON（例如 ping） */
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.setStatus("closed");
      this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect() {
    if (this.timer) return;
    const delay = Math.min(10000, 500 * 2 ** this.retry++);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.handlers.size > 0) this.ensure();
    }, delay);
  }

  private sendSubscribe(channels: string[], action: "subscribe" | "unsubscribe" = "subscribe") {
    if (!channels.length || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ action, channels }));
  }

  subscribe(channels: string[], h: Handler): () => void {
    const added: string[] = [];
    for (const c of channels) {
      let set = this.handlers.get(c);
      if (!set) {
        set = new Set();
        this.handlers.set(c, set);
      }
      if (set.size === 0 && c !== "*") added.push(c);
      set.add(h);
    }
    this.ensure();
    this.sendSubscribe(added);
    return () => {
      const removed: string[] = [];
      for (const c of channels) {
        const set = this.handlers.get(c);
        set?.delete(h);
        if (set && set.size === 0) {
          this.handlers.delete(c);
          if (c !== "*") removed.push(c);
        }
      }
      this.sendSubscribe(removed, "unsubscribe");
    };
  }

  /** 切換身分後重連，讓後端拿到新的 role */
  reconnect() {
    if (MOCK) return;
    this.ws?.close();
  }
}

export const wsClient = new WsClient();

/** 訂閱一或多個頻道；handler 永遠用最新的 closure */
export function useWs(channels: string[], handler: Handler, enabled = true) {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  const key = channels.join("|");
  useEffect(() => {
    if (!enabled) return;
    return wsClient.subscribe(key.split("|").filter(Boolean), (m) => ref.current(m));
  }, [key, enabled]);
}

export function useWsStatus(): WsStatus {
  return useSyncExternalStore(wsClient.onStatus, wsClient.getStatus, () => (MOCK ? "mock" : "closed"));
}
