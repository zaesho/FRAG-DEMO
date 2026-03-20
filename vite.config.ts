import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  root: resolve(__dirname, "web"),
  build: {
    outDir: resolve(__dirname, "dist/client"),
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5000,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:5050",
      "/clips": "http://127.0.0.1:5050",
    },
  },
});
