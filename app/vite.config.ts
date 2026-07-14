import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Puerto fijo 1420: Tauri lo espera en dev (ver src-tauri/tauri.conf.json).
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
});
