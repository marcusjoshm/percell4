import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// `base: './'` is required so the built bundle loads via Tauri's
// `file://` protocol inside the WebView. Without it, asset URLs are
// absolute (`/assets/...`) and the WebView serves them from the wrong
// root.
export default defineConfig({
  base: "./",
  plugins: [
    TanStackRouterVite({
      target: "react",
      autoCodeSplitting: true,
      routesDirectory: "src/routes",
      generatedRouteTree: "src/routeTree.gen.ts",
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 8080,
    strictPort: true,
  },
  build: {
    target: "esnext",
    outDir: "dist",
  },
});
