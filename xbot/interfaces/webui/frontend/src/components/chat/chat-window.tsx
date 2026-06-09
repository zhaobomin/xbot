import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { nanoid } from "nanoid";
import { useQueryClient } from "@tanstack/react-query";
import { useChatStore } from "../../stores/chat-store";
import { useGatewayBaseUrl } from "../../stores/gateway-store";
import { ChatWebSocket, type WsMessage } from "../../lib/ws";
import { isWritableClientSession } from "../../lib/client-runtime";
import { MessageBubble } from "./message-bubble";
import { ChatInput } from "./chat-input";
import { useRevokeMessage } from "../../hooks/use-sessions";
import { ArrowDown, Eye, Monitor } from "lucide-react";
import { getChannelIcon } from "../../lib/channel-icons";
import { StatusDot } from "../business/status-dot";

function sessionTitle(sessionKey?: string | null): string {
    if (!sessionKey) return "default";
    const parts = sessionKey.split(":");
    const namespace = parts[0];
    if ((namespace === "web" || namespace === "app") && parts[2]) return parts[2];
    return parts[parts.length - 1] || sessionKey;
}

function sessionNamespace(sessionKey?: string | null): string {
    return sessionKey?.split(":")[0] || "app";
}

export function ChatWindow() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const gatewayBaseUrl = useGatewayBaseUrl();
    const {
        currentSessionKey,
        messages,
        showToolMessages,
        addMessage,
        appendAssistantText,
        setWaiting,
        setProgress,
        setCurrentSession,
        toggleToolMessages,
    } = useChatStore();

    const sessionState = useChatStore((s) => {
        const key = s.currentSessionKey ?? "";
        return s.sessionStates[key] ?? { isWaiting: false, progressText: "" };
    });
    const isWaiting = sessionState.isWaiting;
    const progressText = sessionState.progressText;
    const readOnly = !isWritableClientSession(currentSessionKey);
    const namespace = sessionNamespace(currentSessionKey);
    const title = sessionTitle(currentSessionKey);
    const ChannelIcon = getChannelIcon(currentSessionKey ?? "app:default");

    const visibleMessages = showToolMessages
        ? messages
        : messages.filter(
            (m) =>
                m.role !== "tool" && m.role !== "sub_tool" && m.role !== "system"
        );

    const wsRef = useRef<ChatWebSocket | null>(null);
    const assistantMsgIdRef = useRef<string | null>(null);
    const bottomRef = useRef<HTMLDivElement>(null);
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const handleWsMessageRef = useRef<(msg: WsMessage) => void>(() => { });
    const [isConnected, setIsConnected] = useState(false);
    const revokeMessage = useRevokeMessage();
    const isNearBottomRef = useRef(true);
    const [showScrollBtn, setShowScrollBtn] = useState(false);

    // Track scroll position to determine if user is near bottom
    useEffect(() => {
        const el = scrollContainerRef.current;
        if (!el) return;
        const handleScroll = () => {
            const threshold = 120;
            const nearBottom =
                el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
            isNearBottomRef.current = nearBottom;
            setShowScrollBtn((prev) => {
                const next = !nearBottom;
                return prev === next ? prev : next;
            });
        };
        el.addEventListener("scroll", handleScroll, { passive: true });
        return () => el.removeEventListener("scroll", handleScroll);
    }, []);

    useEffect(() => {
        const ws = new ChatWebSocket(
            (msg) => handleWsMessageRef.current(msg),
            (connected) => setIsConnected(connected)
        );
        wsRef.current = ws;
        ws.connect(useChatStore.getState().currentSessionKey ?? undefined);
        return () => {
            ws.disconnect();
        };
    }, []);

    useEffect(() => {
        if (currentSessionKey) {
            wsRef.current?.setSession(currentSessionKey);
        }
    }, [currentSessionKey]);

    useEffect(() => {
        const el = scrollContainerRef.current;
        if (el && isNearBottomRef.current) el.scrollTop = el.scrollHeight;
    }, [messages, progressText]);

    const handleWsMessage = useCallback(
        (msg: WsMessage) => {
            const msgSessionKey = msg.session_key;
            const currentKey = useChatStore.getState().currentSessionKey;
            const isCurrentSession = !msgSessionKey || msgSessionKey === currentKey;

            if (msg.type === "session_info") {
                if (msg.session_key && msg.session_key !== currentKey) {
                    setCurrentSession(msg.session_key);
                }
            } else if (msg.type === "progress") {
                const targetKey = msgSessionKey || currentKey || "";

                if (msg.event_type === "content_delta") {
                    setProgress("", targetKey);
                    if (isCurrentSession && msg.content) {
                        let assistantId = assistantMsgIdRef.current;
                        if (!assistantId) {
                            assistantId = nanoid();
                            assistantMsgIdRef.current = assistantId;
                            addMessage({
                                id: assistantId,
                                role: "assistant",
                                content: "",
                                timestamp: new Date().toISOString(),
                                isStreaming: true,
                            });
                        }
                        appendAssistantText(assistantId, msg.content);
                    }
                    return;
                }

                if (isCurrentSession && msg.content?.trim()) {
                    if (msg.tool_hint) {
                        addMessage({
                            id: nanoid(),
                            role: "tool",
                            content: msg.content,
                            timestamp: new Date().toISOString(),
                        });
                    } else {
                        addMessage({
                            id: nanoid(),
                            role: "assistant",
                            content: msg.content,
                            timestamp: new Date().toISOString(),
                        });
                    }
                }
                setProgress(msg.content ?? "", targetKey);
            } else if (msg.type === "subagent_progress") {
                if (isCurrentSession) {
                    if (msg.content?.trim()) {
                        addMessage({
                            id: nanoid(),
                            role: "tool",
                            content: msg.content,
                            timestamp: new Date().toISOString(),
                            isSubAgent: true,
                        });
                    }
                }
            } else if (msg.type === "done") {
                const targetKey = msgSessionKey || currentKey || "";
                setProgress("", targetKey);
                setWaiting(false, targetKey);

                const streamingAssistantId = assistantMsgIdRef.current;
                if (assistantMsgIdRef.current) {
                    useChatStore
                        .getState()
                        .setStreaming(assistantMsgIdRef.current, false);
                    assistantMsgIdRef.current = null;
                }

                if (isCurrentSession && !streamingAssistantId) {
                    if (msg.content?.trim()) {
                        addMessage({
                            id: nanoid(),
                            role: "assistant",
                            content: msg.content,
                            timestamp: new Date().toISOString(),
                        });
                    }
                }

                qc.invalidateQueries({ queryKey: ["sessions"] });
                if (targetKey) {
                    qc.invalidateQueries({
                        queryKey: ["sessions", gatewayBaseUrl, targetKey, "messages"],
                    });
                }
            } else if (msg.type === "error") {
                const targetKey = msgSessionKey || currentKey || "";
                setProgress("", targetKey);
                setWaiting(false, targetKey);

                if (isCurrentSession) {
                    addMessage({
                        id: nanoid(),
                        role: "assistant",
                        content: `⚠️ ${msg.content ?? msg.error ?? t("common.error")}`,
                        timestamp: new Date().toISOString(),
                    });
                }
            } else if (msg.type === "revoke_ok") {
                const targetKey = msgSessionKey || currentKey || "";
                qc.invalidateQueries({
                    queryKey: ["sessions", gatewayBaseUrl, targetKey, "messages"],
                });
                qc.invalidateQueries({ queryKey: ["sessions"] });
            }
        },
        [addMessage, appendAssistantText, gatewayBaseUrl, qc, setCurrentSession, setProgress, setWaiting, t]
    );

    useEffect(() => {
        handleWsMessageRef.current = handleWsMessage;
    }, [handleWsMessage]);

    const handleSend = useCallback(
        (content: string) => {
            if (readOnly) return;
            if (!wsRef.current?.isConnected) {
                wsRef.current?.connect();
            }
            addMessage({
                id: nanoid(),
                role: "user",
                content,
                timestamp: new Date().toISOString(),
            });
            const key = currentSessionKey ?? "";
            setWaiting(true, key);
            setProgress(t("chat.thinking"), key);
            wsRef.current?.send(content, currentSessionKey ?? undefined);
        },
        [addMessage, currentSessionKey, readOnly, setProgress, setWaiting, t]
    );

    const handleStop = useCallback(() => {
        const key = currentSessionKey ?? "";
        wsRef.current?.cancel(key);
        setWaiting(false, key);
        setProgress("", key);
    }, [currentSessionKey, setProgress, setWaiting]);

    const handleRevoke = useCallback(
        (messageId: string) => {
            if (!currentSessionKey) return;
            const msg = messages.find((m) => m.id === messageId);
            if (!msg) return;

            const serverIndex = msg.serverIndex;
            if (serverIndex === undefined) return;

            if (serverIndex >= 0) {
                revokeMessage.mutate(
                    { key: currentSessionKey, index: serverIndex },
                    {
                        onSuccess: () => {
                            const state = useChatStore.getState();
                            const idx = state.messages.findIndex((m) => m.id === messageId);
                            if (idx >= 0) {
                                const newMsgs = [...state.messages];
                                if (msg.role === "user") {
                                    let end = idx + 1;
                                    while (end < newMsgs.length && newMsgs[end].role !== "user") {
                                        end++;
                                    }
                                    newMsgs.splice(idx, end - idx);
                                } else {
                                    newMsgs.splice(idx, 1);
                                }
                                useChatStore.getState().setMessages(newMsgs);
                            }
                        },
                    }
                );
            }
        },
        [currentSessionKey, messages, revokeMessage]
    );

    const scrollToBottom = useCallback(() => {
        const el = scrollContainerRef.current;
        if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }, []);

    return (
        <div className="flex flex-1 min-h-0 flex-col bg-card">
            <div className="hidden h-14 shrink-0 items-center justify-between border-b border-border/40 bg-card px-6 md:flex">
                <div className="flex min-w-0 items-center gap-3">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted/70 text-muted-foreground">
                        <ChannelIcon className="h-4 w-4" />
                    </div>
                    <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-foreground">
                            {title}
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                            <span className="uppercase">{namespace}</span>
                            {readOnly && (
                                <>
                                    <span className="h-1 w-1 rounded-full bg-border" />
                                    <span className="inline-flex items-center gap-1">
                                        <Eye className="h-3 w-3" />
                                        只读
                                    </span>
                                </>
                            )}
                        </div>
                    </div>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <StatusDot
                        tone={isConnected ? "success" : "danger"}
                        label={isConnected ? "已连接" : "未连接"}
                    />
                    {isWaiting && (
                        <span className="rounded-md border border-border px-2 py-1 text-muted-foreground">
                            运行中
                        </span>
                    )}
                    {!currentSessionKey && <Monitor className="h-4 w-4" />}
                </div>
            </div>
            <div className="relative flex-1 min-h-0">
                <div
                    ref={scrollContainerRef}
                    className="h-full overflow-y-auto overflow-x-hidden bg-card px-4 py-6 md:px-8"
                >
                    {messages.length === 0 ? (
                        <div className="mx-auto flex min-h-[360px] max-w-7xl flex-col items-center justify-center gap-4 pt-[8vh]">
                            <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-muted/70 ring-1 ring-border/40">
                                <span className="text-2xl text-primary select-none leading-none">
                                    ✦
                                </span>
                            </div>
                            <div className="text-center space-y-1.5">
                                <p className="font-semibold text-foreground/90">xbot</p>
                                <p className="text-sm text-muted-foreground">
                                    {t("chat.noMessages")}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <div className="mx-auto max-w-7xl space-y-5">
                            {visibleMessages.map((msg) => (
                                <MessageBubble
                                    key={msg.id}
                                    message={msg}
                                    onRevoke={readOnly ? undefined : handleRevoke}
                                />
                            ))}
                        </div>
                    )}
                    {isWaiting && progressText && (
                        <div className="mx-auto mt-4 flex max-w-7xl items-start gap-3 px-1">
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted/70 text-xs font-bold text-foreground/70">
                                x
                            </div>
                            <div className="flex items-center gap-2 rounded-xl bg-muted/70 px-4 py-2.5 text-sm text-muted-foreground">
                                <span className="flex gap-1">
                                    <span className="h-1.5 w-1.5 rounded-full bg-current animate-bounce [animation-delay:0ms]" />
                                    <span className="h-1.5 w-1.5 rounded-full bg-current animate-bounce [animation-delay:150ms]" />
                                    <span className="h-1.5 w-1.5 rounded-full bg-current animate-bounce [animation-delay:300ms]" />
                                </span>
                                <span className="truncate max-w-xs">{progressText}</span>
                            </div>
                        </div>
                    )}
                    <div ref={bottomRef} />
                </div>
                {showScrollBtn && (
                    <button
                        onClick={scrollToBottom}
                        className="absolute bottom-3 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-border/50 bg-card/95 text-muted-foreground shadow-sm backdrop-blur-sm transition-all duration-200 hover:text-foreground"
                        aria-label="Scroll to bottom"
                    >
                        <ArrowDown className="h-4 w-4" />
                    </button>
                )}
            </div>
            <ChatInput
                onSend={handleSend}
                disabled={isWaiting || readOnly}
                onStop={handleStop}
                isWaiting={isWaiting}
                isConnected={isConnected}
                showToolMessages={showToolMessages}
                onToggleToolMessages={toggleToolMessages}
                readOnly={readOnly}
            />
        </div>
    );
}
