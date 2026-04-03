import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { nanoid } from "nanoid";
import { useQueryClient } from "@tanstack/react-query";
import { useChatStore } from "../../stores/chat-store";
import { ChatWebSocket, type WsMessage } from "../../lib/ws";
import { MessageBubble } from "./message-bubble";
import { ChatInput } from "./chat-input";
import { useRevokeMessage } from "../../hooks/use-sessions";

export function ChatWindow() {
    const { t } = useTranslation();
    const qc = useQueryClient();
    const {
        currentSessionKey,
        messages,
        showToolMessages,
        addMessage,
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
        if (el) el.scrollTop = el.scrollHeight;
    }, [messages, progressText]);

    const handleWsMessage = useCallback(
        (msg: WsMessage) => {
            const msgSessionKey = msg.session_key;
            const currentKey = useChatStore.getState().currentSessionKey;

            if (msg.type === "session_info") {
                if (msg.session_key && msg.session_key !== currentKey) {
                    setCurrentSession(msg.session_key);
                }
            } else if (msg.type === "progress") {
                if (msg.content?.trim()) {
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
                const targetKey = msgSessionKey || currentKey || "";
                setProgress(msg.content ?? "", targetKey);
            } else if (msg.type === "subagent_progress") {
                if (!msgSessionKey || msgSessionKey === currentKey) {
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

                if (assistantMsgIdRef.current) {
                    useChatStore
                        .getState()
                        .setStreaming(assistantMsgIdRef.current, false);
                    assistantMsgIdRef.current = null;
                }

                if (!msgSessionKey || msgSessionKey === currentKey) {
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
                        queryKey: ["sessions", targetKey, "messages"],
                    });
                }
            } else if (msg.type === "error") {
                const targetKey = msgSessionKey || currentKey || "";
                setProgress("", targetKey);
                setWaiting(false, targetKey);

                if (!msgSessionKey || msgSessionKey === currentKey) {
                    addMessage({
                        id: nanoid(),
                        role: "assistant",
                        content: `\u26a0\ufe0f ${msg.content ?? t("common.error")}`,
                        timestamp: new Date().toISOString(),
                    });
                }
            } else if (msg.type === "revoke_ok") {
                const targetKey = msgSessionKey || currentKey || "";
                qc.invalidateQueries({
                    queryKey: ["sessions", targetKey, "messages"],
                });
                qc.invalidateQueries({ queryKey: ["sessions"] });
            }
        },
        [addMessage, qc, setCurrentSession, setProgress, setWaiting, t]
    );

    useEffect(() => {
        handleWsMessageRef.current = handleWsMessage;
    }, [handleWsMessage]);

    const handleSend = useCallback(
        (content: string) => {
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
        [addMessage, currentSessionKey, setProgress, setWaiting, t]
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

    return (
        <div className="flex flex-1 min-h-0 flex-col">
            <div
                ref={scrollContainerRef}
                className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 py-6"
            >
                {messages.length === 0 ? (
                    <div className="flex h-full min-h-[300px] flex-col items-center justify-center gap-5">
                        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/15 shadow-inner">
                            <span className="text-3xl text-primary select-none leading-none">
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
                    <div className="space-y-4">
                        {visibleMessages.map((msg) => (
                            <MessageBubble
                                key={msg.id}
                                message={msg}
                                onRevoke={handleRevoke}
                            />
                        ))}
                    </div>
                )}
                {isWaiting && progressText && (
                    <div className="mt-4 flex items-start gap-3 px-4">
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/12 text-xs font-black lowercase text-primary shadow-sm">
                            x
                        </div>
                        <div className="rounded-2xl rounded-tl-sm bg-muted px-4 py-2.5 text-sm text-muted-foreground flex items-center gap-2">
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
            <ChatInput
                onSend={handleSend}
                disabled={isWaiting}
                onStop={handleStop}
                isWaiting={isWaiting}
                isConnected={isConnected}
                showToolMessages={showToolMessages}
                onToggleToolMessages={toggleToolMessages}
            />
        </div>
    );
}
