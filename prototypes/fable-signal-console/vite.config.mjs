import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const modelApiBase = process.env.FABLE_SIGNAL_CONSOLE_API_BASE || "http://127.0.0.1:8643";

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
    proxy: {
      "/api/model": {
        target: modelApiBase,
        changeOrigin: false,
      },
      "/api/market": {
        target: modelApiBase,
        changeOrigin: false,
      },
    },
  },
  plugins: [react()],
});
