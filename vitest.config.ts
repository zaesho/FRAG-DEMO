import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./web/test/setup.ts"],
    include: ["web/src/**/*.test.tsx", "server/**/*.test.ts"],
  },
});
