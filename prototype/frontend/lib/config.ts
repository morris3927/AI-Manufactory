export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000").replace(/\/$/, "");

export const MOCK = process.env.NEXT_PUBLIC_MOCK === "1" || process.env.NEXT_PUBLIC_MOCK === "true";

export const WS_URL = (() => {
  const u = API_BASE.replace(/^http/, "ws");
  return `${u}/ws`;
})();
