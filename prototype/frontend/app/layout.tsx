import type { Metadata, Viewport } from "next";
import { AppHeader, FloatingSimBadge } from "@/components/app-header";
import { Toaster } from "@/components/toast";
import { THEME_COLOR, THEME_INIT_SCRIPT } from "@/lib/theme-config";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sentinel AI 監控中心",
  description: "智慧工廠異常偵測與派工原型（模擬資料）",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: THEME_COLOR.light,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme 由 inline script 在 hydrate 前改掉，所以 <html> 要 suppressHydrationWarning
    <html lang="zh-Hant-TW" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col">
        <AppHeader />
        <main className="mx-auto w-full max-w-[1800px] flex-1 px-3 py-3 sm:px-4 sm:py-4">{children}</main>
        <FloatingSimBadge />
        <Toaster />
      </body>
    </html>
  );
}
