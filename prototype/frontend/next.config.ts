import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
