"use client";

import { useSyncExternalStore } from "react";
import { THEME_COLOR, THEME_KEY, type Theme } from "./theme-config";

// 淺色／深色主題。預設淺色；選擇存在 localStorage，<html data-theme> 由 layout 的 inline script 先設好（避免閃一下）。
export type { Theme };

const listeners = new Set<() => void>();

function current(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

export function setTheme(t: Theme) {
  const el = document.documentElement;
  el.setAttribute("data-theme", t);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[t]);
  try {
    window.localStorage.setItem(THEME_KEY, t);
  } catch {
    /* 私密視窗等情況，只是不記住 */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, current, () => "light");
}
