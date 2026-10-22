import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // e2e／Demo 腳本用另一個輸出目錄 build（NEXT_PUBLIC_API_BASE 不同），不蓋掉平常的 .next
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // 全部頁面都是即時資料的 Client Component，不需要 Cache Components。
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
