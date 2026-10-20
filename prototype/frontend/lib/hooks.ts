"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
  reload: () => void;
  setData: (fn: (d: T | undefined) => T | undefined) => void;
}

/** 讀取資料；reload() 重新抓取（例如收到 WebSocket 事件時） */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | null>(null);
  const [done, setDone] = useState(false);
  const [key, setKey] = useState(0);
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  const depKey = JSON.stringify(deps);
  useEffect(() => {
    let alive = true;
    fnRef
      .current()
      .then((d) => {
        if (!alive) return;
        setDataState(d);
        setError(null);
        setDone(true);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setDone(true);
      });
    return () => {
      alive = false;
    };
  }, [depKey, key]);
  const reload = useCallback(() => setKey((k) => k + 1), []);
  const setData = useCallback((f: (d: T | undefined) => T | undefined) => setDataState(f), []);
  return { data, error, loading: !done, reload, setData };
}

/** 節流的 reload：短時間內多個 WS 事件只重抓一次 */
export function useThrottled(fn: () => void, ms = 400) {
  const t = useRef<ReturnType<typeof setTimeout> | null>(null);
  const f = useRef(fn);
  useEffect(() => {
    f.current = fn;
  });
  return useCallback(() => {
    if (t.current) return;
    t.current = setTimeout(() => {
      t.current = null;
      f.current();
    }, ms);
  }, [ms]);
}

// 全域每秒 tick（倒數計時、經過時間）
const tickListeners = new Set<() => void>();
let tickTimer: ReturnType<typeof setInterval> | null = null;
function subscribeTick(l: () => void) {
  tickListeners.add(l);
  if (!tickTimer) tickTimer = setInterval(() => tickListeners.forEach((x) => x()), 1000);
  return () => {
    tickListeners.delete(l);
    if (!tickListeners.size && tickTimer) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  };
}

/** 目前時間（毫秒，取整到秒）；伺服器端渲染時為 0 */
export function useNow(): number {
  return useSyncExternalStore(
    subscribeTick,
    () => Math.floor(Date.now() / 1000) * 1000,
    () => 0,
  );
}
