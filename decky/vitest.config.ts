import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  resolve: {
    // Decky injects these at runtime; tests use hand-written stand-ins.
    alias: {
      "@decky/api": here("./test/mocks/decky-api.ts"),
      "@decky/ui": here("./test/mocks/decky-ui.tsx"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["test/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    coverage: { provider: "v8", include: ["src/**"], reporter: ["text", "json-summary"] },
  },
});
