import type { Metadata, Viewport } from "next";
import { AppHeader, FloatingSimBadge } from "@/components/app-header";
import { Toaster } from "@/components/toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sentinel AI 戰情中心",
  description: "智慧工廠異常偵測與派工原型（模擬資料）",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#070b10",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-Hant-TW" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <AppHeader />
        <main className="mx-auto w-full max-w-[1800px] flex-1 px-3 py-3 sm:px-4 sm:py-4">{children}</main>
        <FloatingSimBadge />
        <Toaster />
      </body>
    </html>
  );
}
