import { useAuthStore } from "../stores/auth-store";
import { getGatewayWebSocketUrl, useGatewayStore } from "../stores/gateway-store";

export { getGatewayWebSocketUrl, useGatewayStore };

export type WsMessageType = "session_info" | "progress" | "subagent_progress" | "done" | "error" | "revoke_ok";

export interface WsMessage {
    type: WsMessageType;
    content?: string;
    error?: string;
    session_key?: string;
    tool_hint?: boolean;
    event_type?: string;
    event_data?: unknown;
    index?: number;
}

type MessageHandler = (msg: WsMessage) => void;
type StatusHandler = (connected: boolean) => void;

export class ChatWebSocket {
    private ws: WebSocket | null = null;
    private onMessage: MessageHandler;
    private onStatusChange: StatusHandler | null = null;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private shouldReconnect = false;
    private url: string;
    private sessionKey: string | null = null;

    constructor(onMessage: MessageHandler, onStatusChange?: StatusHandler) {
        this.onMessage = onMessage;
        this.onStatusChange = onStatusChange ?? null;
        this.url = getGatewayWebSocketUrl();
    }

    connect(sessionKey?: string) {
        const token = useAuthStore.getState().token;

        if (sessionKey) this.sessionKey = sessionKey;

        this.shouldReconnect = true;

        const params = new URLSearchParams();
        this.url = getGatewayWebSocketUrl();
        if (token) params.set("token", token);
        if (this.sessionKey) params.set("session", this.sessionKey);
        const query = params.toString();
        const wsUrl = query ? `${this.url}?${query}` : this.url;
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            if (this.reconnectTimer) {
                clearTimeout(this.reconnectTimer);
                this.reconnectTimer = null;
            }
            this.onStatusChange?.(true);
        };

        this.ws.onmessage = (event) => {
            try {
                const msg: WsMessage = JSON.parse(event.data);
                this.onMessage(msg);
            } catch {
                // ignore malformed frames
            }
        };

        this.ws.onclose = () => {
            this.onStatusChange?.(false);
            if (this.shouldReconnect) {
                this.reconnectTimer = setTimeout(() => this.connect(), 3000);
            }
        };

        this.ws.onerror = () => {
            this.ws?.close();
        };
    }

    send(content: string, sessionKey?: string) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: "message", content, session_key: sessionKey }));
        }
    }

    setSession(sessionKey: string) {
        this.sessionKey = sessionKey;
    }

    cancel(sessionKey?: string) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: "cancel", session_key: sessionKey }));
        }
    }

    revoke(sessionKey: string, index: number) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: "revoke", session_key: sessionKey, index }));
        }
    }

    disconnect() {
        this.shouldReconnect = false;
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        this.ws?.close();
        this.ws = null;
    }

    get isConnected() {
        return this.ws?.readyState === WebSocket.OPEN;
    }
}
