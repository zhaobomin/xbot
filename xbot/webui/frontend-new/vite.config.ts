import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
    plugins: [
        react(),
        VitePWA({
            registerType: "autoUpdate",
            includeAssets: [
                "icon.png",
                "logo.png",
                "app-64x64.png",
                "app-120x120.png",
                "app-144x144.png",
                "app-152x152.png",
                "app-180x180.png",
                "app-192x192.png",
                "app-512x512.png",
            ],
            workbox: {
                navigateFallbackDenylist: [/^\/api\//],
                runtimeCaching: [
                    {
                        urlPattern: /^\/api\//,
                        handler: "NetworkOnly",
                    },
                ],
                globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
            },
            devOptions: {
                enabled: true,
                type: "module",
            },
            manifest: {
                name: "XBot",
                short_name: "XBot",
                description: "xbot AI Agent Management Interface",
                theme_color: "#462584",
                background_color: "#f8f7fc",
                display: "standalone",
                start_url: "/",
                scope: "/",
                orientation: "portrait",
                icons: [
                    {
                        src: "app-64x64.png",
                        sizes: "64x64",
                        type: "image/png",
                        purpose: "any",
                    },
                    {
                        src: "app-192x192.png",
                        sizes: "192x192",
                        type: "image/png",
                        purpose: "any",
                    },
                    {
                        src: "app-512x512.png",
                        sizes: "512x512",
                        type: "image/png",
                        purpose: "any",
                    },
                ],
            },
        }),
    ],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    server: {
        host: true,
        port: 5174,
        proxy: {
            "/api": {
                target: "http://localhost:18780",
                changeOrigin: true,
            },
            "/ws": {
                target: "ws://localhost:18780",
                ws: true,
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: "dist",
        sourcemap: false,
        rollupOptions: {
            output: {
                manualChunks(id) {
                    if (id.includes("node_modules")) {
                        if (
                            id.includes("react-markdown") ||
                            id.includes("rehype-highlight") ||
                            id.includes("rehype-raw") ||
                            id.includes("rehype-") ||
                            id.includes("remark-") ||
                            id.includes("unified") ||
                            id.includes("micromark") ||
                            id.includes("mdast") ||
                            id.includes("hast") ||
                            id.includes("hast-util") ||
                            id.includes("unist") ||
                            id.includes("vfile") ||
                            id.includes("highlight.js")
                        ) {
                            return "vendor-markdown";
                        }
                        if (id.includes("@radix-ui")) {
                            return "vendor-radix";
                        }
                        if (id.includes("lucide-react")) {
                            return "vendor-icons";
                        }
                        if (
                            id.includes("i18next") ||
                            id.includes("react-i18next")
                        ) {
                            return "vendor-i18n";
                        }
                        if (
                            id.includes("node_modules/react/") ||
                            id.includes("node_modules/react-dom/") ||
                            id.includes("node_modules/react-router") ||
                            id.includes("node_modules/scheduler/")
                        ) {
                            return "vendor-react";
                        }
                        if (id.includes("@tanstack")) {
                            return "vendor-query";
                        }
                    }
                },
            },
        },
    },
});
