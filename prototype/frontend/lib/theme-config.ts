// 淺色／深色主題的共用常數。layout（Server Component）也要用，所以不放在 "use client" 的 lib/theme.ts。
export type Theme = "light" | "dark";

export const THEME_KEY = "sentinel.theme";
/** 網址列／狀態列顏色，跟 globals.css 的 --background 一致 */
export const THEME_COLOR: Record<Theme, string> = { light: "#eef1f5", dark: "#070b10" };

/** 放在 <head> 的 inline script：第一次繪製前讀 localStorage 設 data-theme，沒選過就維持淺色 */
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)});if(t==="dark"||t==="light"){document.documentElement.setAttribute("data-theme",t);var m=document.querySelector('meta[name="theme-color"]');if(m)m.setAttribute("content",t==="dark"?${JSON.stringify(THEME_COLOR.dark)}:${JSON.stringify(THEME_COLOR.light)})}}catch(e){}})()`;
