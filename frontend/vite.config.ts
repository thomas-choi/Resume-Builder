import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API serves the built bundle itself (src/api/main.py mounts dist/ at "/"),
// so the app calls same-origin paths and needs no base URL. In `npm run dev`
// the Vite server is a different port, so /ingest & co. are proxied to the API.
const API_URL = process.env.API_URL || "http://localhost:8000";

// Where the dev/preview server itself listens. Defaults match Vite's own
// (loopback:5173) so nothing is exposed unless asked; UI_HOST=0.0.0.0 makes the
// UI reachable from another machine. Vite's --host/--port flags still win.
const UI_HOST = process.env.UI_HOST || "localhost";
const UI_PORT = Number(process.env.UI_PORT || 5173);
// Serving on a LAN IP works out of the box; a DNS name (a tunnel, an /etc/hosts
// alias) is rejected by Vite's host check unless it is listed here.
const UI_ALLOWED_HOSTS = (process.env.UI_ALLOWED_HOSTS || "")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

const server = {
  host: UI_HOST,
  port: UI_PORT,
  // Fail loudly instead of silently drifting to 5174 when the port is taken —
  // the configured port is the one other machines were told to use.
  strictPort: true,
  ...(UI_ALLOWED_HOSTS.length ? { allowedHosts: UI_ALLOWED_HOSTS } : {}),
};

export default defineConfig({
  plugins: [react()],
  server: {
    ...server,
    proxy: Object.fromEntries(
      ["/ingest", "/profile", "/tailor", "/document", "/healthz"].map((path) => [
        path,
        { target: API_URL, changeOrigin: true },
      ]),
    ),
  },
  // `npm run preview` serves the built dist/ — same knobs, so a LAN smoke test
  // of the production bundle needs no extra flags.
  preview: server,
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
