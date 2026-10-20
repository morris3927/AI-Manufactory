"use client";

import { useSyncExternalStore } from "react";
import type { Role } from "./types";

// Demo 不做登入：右上角切換身分，送出 X-Role / X-Person-Id（§8.3）。
// 人員 ID 與 data/simulated/roster.csv 的虛構人員對應（假設 ID 格式）。
export interface Persona {
  role: Role;
  personId: string;
  name: string;
}

export const PERSONAS: Record<Role, Persona> = {
  technician: { role: "technician", personId: "P-T01", name: "林志明" },
  leader: { role: "leader", personId: "P-L01", name: "陳美玲" },
  supervisor: { role: "supervisor", personId: "P-S01", name: "王建國" },
  judge: { role: "judge", personId: "JUDGE", name: "評審" },
};

const KEY = "sentinel.role";
let current: Role = "technician";
let loaded = false;
const listeners = new Set<() => void>();

function load() {
  if (loaded || typeof window === "undefined") return;
  loaded = true;
  try {
    const v = window.localStorage.getItem(KEY) as Role | null;
    if (v && v in PERSONAS) current = v;
  } catch {
    /* 私密視窗等情況，忽略 */
  }
}

export function getPersona(): Persona {
  load();
  return PERSONAS[current];
}

export function setRole(r: Role) {
  current = r;
  try {
    window.localStorage.setItem(KEY, r);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function usePersona(): Persona {
  return useSyncExternalStore(
    subscribe,
    () => getPersona(),
    () => PERSONAS.technician,
  );
}

export function useReadOnly(): boolean {
  return usePersona().role === "judge";
}
