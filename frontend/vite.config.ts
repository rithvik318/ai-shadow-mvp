import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * The dev server proxies `/api` to the backend.
 *
 * The backend registers no CORS middleware, so a browser would refuse a
 * direct cross-origin call from :5173 to :8000. Proxying keeps every request
 * same-origin and needs no backend change; in production the built files are
 * expected to be served from the same origin as the API, or behind a reverse
 * proxy that does the same thing. See README.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_ORIGIN ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
