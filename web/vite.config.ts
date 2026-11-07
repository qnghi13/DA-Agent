import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI and the API share one origin through this proxy, so the API needs no CORS.
const apiTarget = process.env.DA_AGENT_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": { target: apiTarget, changeOrigin: true },
    },
  },
});
