import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
    plugins: [react()],
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
