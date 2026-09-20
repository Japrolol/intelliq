import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: [
        "brand/intelliq-mark.png",
        "brand/intelliq-mark-192.png",
        "brand/intelliq-mark-512.png",
        "brand/intelliq-logo.png",
      ],
      manifest: {
        name: "IntelliQ decision desk",
        short_name: "IntelliQ",
        description: "A decision-first portfolio view for construction operations.",
        start_url: "/overview",
        display: "standalone",
        theme_color: "#F6F4EF",
        background_color: "#FAF9F6",
        icons: [
          {
            src: "/brand/intelliq-mark-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/brand/intelliq-mark-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
        ],
      },
      workbox: {
        // Precache the static shell only. API responses and credentials are never cached.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2,webmanifest}"],
        runtimeCaching: [],
      },
      devOptions: { enabled: false },
    }),
  ],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8002",
    },
  },
});
