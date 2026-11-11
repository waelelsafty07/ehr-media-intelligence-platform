/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Backend URL for the dev proxy; override with API_PROXY_TARGET if port 8000 is taken.
declare const process: { env: Record<string, string | undefined> };
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": { target: apiTarget, rewrite: (p) => p.replace(/^\/api/, "") } } },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
