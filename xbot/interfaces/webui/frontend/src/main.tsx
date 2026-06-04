import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";

import App from "./App";
import { ThemeProvider } from "./theme/theme-provider";
import "./i18n";
import "./index.css";

const isLocalWebUI = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

if (isLocalWebUI && "serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker
            .getRegistrations()
            .then((registrations) => Promise.all(registrations.map((registration) => registration.unregister())))
            .catch(() => undefined);

        if ("caches" in window) {
            caches
                .keys()
                .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
                .catch(() => undefined);
        }
    });
}

const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: 1,
            staleTime: 30_000,
        },
    },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <ThemeProvider>
            <QueryClientProvider client={queryClient}>
                <BrowserRouter>
                    <App />
                    <Toaster position="bottom-right" richColors closeButton />
                </BrowserRouter>
            </QueryClientProvider>
        </ThemeProvider>
    </React.StrictMode>
);
