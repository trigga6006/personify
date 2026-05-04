import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";

// Build output goes into the FastAPI static dir so the packaged app can be
// served at /app/. In dev, Vite serves at / so localhost:18766 opens directly.
const OUT_DIR = resolve(__dirname, "..", "personify", "web", "static", "app");
const API_ORIGIN = process.env.PERSONIFY_DEV_API_ORIGIN ?? "http://localhost:18765";
const DEV_PORT = Number(process.env.PERSONIFY_DEV_FRONTEND_PORT ?? "18766");

export default defineConfig(({ command }) => ({
  plugins: [svelte()],
  resolve: {
    alias: {
      $lib: resolve(__dirname, "src/lib"),
      $components: resolve(__dirname, "src/components"),
      $routes: resolve(__dirname, "src/routes"),
    },
  },
  server: {
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      // Dev server proxies API calls to the FastAPI backend so we can run
      // `vault dev` without CORS pain.
      "/api": API_ORIGIN,
      "/health": API_ORIGIN,
      "/sources": API_ORIGIN,
      "/stats": API_ORIGIN,
      "/search": API_ORIGIN,
      "/semantic-search": API_ORIGIN,
      "/items": API_ORIGIN,
      "/timeline": API_ORIGIN,
      "/graph": API_ORIGIN,
      "/static": API_ORIGIN,
    },
  },
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    sourcemap: true,
    target: "esnext",
    // Hashed asset names so /app/ can be cached aggressively without stale-bundle
    // bugs after deploys.
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  base: command === "serve" ? "/" : "/app/",
}));
