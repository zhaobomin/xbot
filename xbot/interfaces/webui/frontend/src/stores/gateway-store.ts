import { create } from "zustand";
import { persist } from "zustand/middleware";

const LOCAL_GATEWAY_URL = "http://127.0.0.1:18780";
const DEV_PORTS = new Set(["5173", "5174"]);

interface GatewayState {
    gatewayUrl: string | null;
    setGatewayUrl: (url: string) => void;
    clearGatewayUrl: () => void;
}

export function normalizeGatewayUrl(url: string): string {
    return url.trim().replace(/\/+$/, "");
}

export function getDefaultGatewayUrl(): string {
    if (
        window.location.protocol === "http:" ||
        window.location.protocol === "https:"
    ) {
        if (!DEV_PORTS.has(window.location.port)) {
            return window.location.origin;
        }
    }
    return LOCAL_GATEWAY_URL;
}

export const useGatewayStore = create<GatewayState>()(
    persist(
        (set) => ({
            gatewayUrl: null,
            setGatewayUrl: (url) => set({ gatewayUrl: normalizeGatewayUrl(url) }),
            clearGatewayUrl: () => set({ gatewayUrl: null }),
        }),
        {
            name: "xbot-gateway",
        }
    )
);

export function getGatewayBaseUrl(): string {
    return useGatewayStore.getState().gatewayUrl || getDefaultGatewayUrl();
}

export function useGatewayBaseUrl(): string {
    const gatewayUrl = useGatewayStore((state) => state.gatewayUrl);
    return gatewayUrl || getDefaultGatewayUrl();
}

export function getGatewayApiBaseUrl(): string {
    return `${getGatewayBaseUrl()}/api`;
}

export function getGatewayWebSocketUrl(path = "/ws/chat"): string {
    const base = getGatewayBaseUrl();
    const wsBase = base.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
    return `${wsBase}${path}`;
}
