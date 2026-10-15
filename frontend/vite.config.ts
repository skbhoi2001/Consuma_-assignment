import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies API calls to the gateway, so no CORS setup is needed.
const gateway = process.env.GATEWAY_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: gateway, changeOrigin: true },
      "/healthz": { target: gateway, changeOrigin: true },
      "/metrics": { target: gateway, changeOrigin: true },
    },
  },
});
