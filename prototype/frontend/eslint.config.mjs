import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // 攝影機畫面是 MJPEG 串流，不能用 next/image。
      "@next/next/no-img-element": "off",
    },
  },
  globalIgnores([".next/**", ".next-*/**", "test-results/**", "playwright-report/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
