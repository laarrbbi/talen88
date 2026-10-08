import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The app talks only to the data API (:8000) and the agent layer (:8002) over HTTP.
// Both run locally in the MVP; their URLs are injected via VITE_* env at build time
// (see src/api/client.ts), defaulting to localhost.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
