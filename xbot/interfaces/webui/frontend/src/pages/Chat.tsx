import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { ChatWindow } from "../components/chat/chat-window";
import { useChatStore, type ChatMessage } from "../stores/chat-store";
import { useSessions, useSessionMessages } from "../hooks/use-sessions";
import { useAuthStore } from "../stores/auth-store";
import { useDeleteSession } from "../hooks/use-sessions";
import { useIsMobile } from "../hooks/use-is-mobile";
import { nanoid } from "nanoid";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { StatusDot } from "../components/business/status-dot";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "../components/ui/dialog";
import { ArrowLeft, MessageSquare, Plus, Search, Trash2 } from "lucide-react";
import { cn, formatDate } from "../lib/utils";
import { CHANNEL_ICONS } from "../lib/channel-icons";
import { createClientSessionKey, getClientSessionPrefix, isDesktopApp } from "../lib/client-runtime";

function channelOf(key: string): string {
    return key.split(":")[0] ?? "web";
}

export default function Chat() {
    const { t } = useTranslation();
    const location = useLocation();
    const navigate = useNavigate();
    const user = useAuthStore((s) => s.user);
    const sessionsOnly = location.pathname === "/sessions";
    const isMobile = useIsMobile();
    const mobileShowChat = useChatStore((s) => s.mobileShowChat);
    const setMobileShowChat = useChatStore((s) => s.setMobileShowChat);
    const { currentSessionKey, setCurrentSession, setMessages } = useChatStore();
    const sessionStates = useChatStore((s) => s.sessionStates);
    const { data: sessions } = useSessions();
    const { data: sessionMsgs, isSuccess: historyLoaded } = useSessionMessages(
        currentSessionKey ?? ""
    );
    const deleteSession = useDeleteSession();
    const loadedKeyRef = useRef<string | null>(null);
    const loadedCountRef = useRef<number>(0);
    const lastSetMsgsRef = useRef<ChatMessage[]>([]);

    useEffect(() => {
        lastSetMsgsRef.current = [];
    }, [currentSessionKey]);

    useEffect(() => {
        if (!currentSessionKey || !historyLoaded) return;
        const serverCount = (sessionMsgs ?? []).length;
        if (
            loadedKeyRef.current === currentSessionKey &&
            serverCount <= loadedCountRef.current
        )
            return;
        loadedKeyRef.current = currentSessionKey;
        loadedCountRef.current = serverCount;
        const msgs = (sessionMsgs ?? [])
            .map((m, idx) => ({ ...m, _serverIdx: idx }))
            .filter(
                (m) =>
                    typeof m.content === "string" &&
                    m.content.trim().length > 0 &&
                    !(m.role === "tool" && m.name === "message") &&
                    !(m.role === "system" && m.content === "[Background task progress]")
            )
            .map((m) => ({
                id: nanoid(),
                role: m.role as "user" | "assistant" | "tool" | "system" | "sub_tool",
                content: m.content as string,
                timestamp: m.timestamp ?? new Date().toISOString(),
                name: m.name ?? undefined,
                serverIndex: m._serverIdx,
            }));
        if (msgs.length > 0) {
            const prevIds = new Set(lastSetMsgsRef.current.map((m) => m.id));
            const localToPreserve = useChatStore
                .getState()
                .messages.filter(
                    (m) =>
                        !prevIds.has(m.id) &&
                        m.role === "assistant" &&
                        m.content.startsWith("\u26a0\ufe0f")
                );
            const merged =
                localToPreserve.length > 0 ? [...msgs, ...localToPreserve] : msgs;
            lastSetMsgsRef.current = merged;
            setMessages(merged);
        }
    }, [currentSessionKey, historyLoaded, sessionMsgs, setMessages]);

    const desktopApp = isDesktopApp();
    const isAdmin = user?.role === "admin";
    const myPrefix = getClientSessionPrefix(user?.id);
    const [search, setSearch] = useState("");
    const [deleteKey, setDeleteKey] = useState<string | null>(null);
    const mySessions = useMemo(
        () =>
            isAdmin && !desktopApp
                ? (sessions ?? [])
                    .slice()
                    .sort((a, b) =>
                        (b.updated_at ?? "").localeCompare(a.updated_at ?? "")
                    )
                : (sessions?.filter((s) => s.key.startsWith(myPrefix)) ?? []),
        [desktopApp, isAdmin, myPrefix, sessions]
    );

    useEffect(() => {
        if (mySessions.length === 0) {
            if (!currentSessionKey?.startsWith(myPrefix)) {
                setCurrentSession(createClientSessionKey(user?.id));
            }
            return;
        }
        const keyExists =
            currentSessionKey &&
            mySessions.some((s) => s.key === currentSessionKey);
        if (!keyExists && !currentSessionKey?.startsWith(myPrefix)) {
            setCurrentSession(mySessions[0].key);
        }
    }, [mySessions, currentSessionKey, setCurrentSession, myPrefix, user?.id]);

    const displaySessions = useMemo(() => {
        const isLocalNew =
            currentSessionKey?.startsWith(myPrefix) &&
            !mySessions.some((s) => s.key === currentSessionKey);
        if (isLocalNew && currentSessionKey) {
            return [
                {
                    key: currentSessionKey,
                    channel: channelOf(currentSessionKey),
                    updated_at: new Date().toISOString(),
                    first_message: undefined,
                    last_message: undefined,
                },
                ...mySessions,
            ];
        }
        return mySessions;
    }, [currentSessionKey, myPrefix, mySessions]);

    const filteredSessions = useMemo(() => {
        if (!search.trim()) return displaySessions;
        const q = search.toLowerCase();
        return displaySessions.filter((s) => {
            const parts = s.key.split(":");
            const label = (parts[parts.length - 1] ?? s.key).toLowerCase();
            const first = (s.first_message ?? "").toLowerCase();
            const preview = (s.last_message ?? "").toLowerCase();
            return label.includes(q) || first.includes(q) || preview.includes(q);
        });
    }, [displaySessions, search]);

    const newChat = () => {
        const hexId = Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) =>
            b.toString(16).padStart(2, "0")
        ).join("");
        const key = createClientSessionKey(user?.id, hexId);
        loadedKeyRef.current = key;
        loadedCountRef.current = 0;
        setCurrentSession(key);
        if (sessionsOnly) navigate("/chat");
        if (isMobile) setMobileShowChat(true);
    };

    const switchSession = (key: string) => {
        setCurrentSession(key);
        if (sessionsOnly) navigate("/chat");
        if (isMobile) setMobileShowChat(true);
    };

    return (
        <div
            className={cn(
                "flex min-h-0",
                isMobile ? "flex-1 flex-col" : "h-full gap-4 p-5"
            )}
        >
            {/* Session sidebar */}
            {(sessionsOnly || isMobile) && (
                <aside
                className={cn(
                    "flex shrink-0 flex-col overflow-hidden",
                    isMobile
                        ? cn(
                            "w-full flex-1 min-h-0 pt-14 bg-background",
                            mobileShowChat && !sessionsOnly && "hidden"
                        )
                        : "min-w-0 flex-1 rounded-xl border bg-card"
                )}
                style={
                    isMobile
                        ? undefined
                        : {
                            width: sessionsOnly ? "100%" : "16rem",
                            minWidth: 0,
                            maxWidth: sessionsOnly ? "none" : "16rem",
                            boxShadow: "var(--shadow-card)",
                        }
                }
            >
                {!isMobile && (
                    <div className="flex shrink-0 items-center justify-between border-b border-border/40 px-3 py-2">
                        <span className="text-sm font-semibold">{t("chat.sessions")}</span>
                        <button
                            onClick={newChat}
                            title={t("chat.newChat")}
                            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        >
                            <Plus className="h-4 w-4" />
                        </button>
                    </div>
                )}

                <div className={cn("shrink-0", isMobile ? "px-4 pt-2 pb-3" : "px-2 py-2")}>
                    <div className="relative">
                        <Search
                            className={cn(
                                "absolute top-1/2 -translate-y-1/2 text-muted-foreground/50",
                                isMobile ? "left-3.5 h-4 w-4" : "left-2 h-3 w-3"
                            )}
                        />
                        <Input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder={t("chat.searchSessions")}
                            className={cn(
                                "border bg-background focus-visible:ring-1",
                                isMobile
                                    ? "h-10 pl-10 text-base rounded-xl"
                                    : "h-7 pl-6 text-xs"
                            )}
                        />
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto min-h-0">
                    <div
                        className={cn(
                            isMobile ? "space-y-0.5 px-2 pb-24" : "space-y-0.5 px-1"
                        )}
                    >
                        {filteredSessions.map((s) => {
                            const channel = s.channel ?? channelOf(s.key);
                            const isWeb = channel === "web";
                            const parts = s.key.split(":");
                            const rawLabel = isWeb
                                ? (parts[2] ?? s.key)
                                : (parts[parts.length - 1] ?? s.key);
                            const maxLen = isMobile ? 28 : 14;
                            const label =
                                s.first_message ||
                                (rawLabel.length > maxLen
                                    ? rawLabel.slice(0, maxLen) + "..."
                                    : rawLabel);
                            const active = s.key === currentSessionKey;
                            const sessionBusy =
                                sessionStates[s.key]?.isWaiting ?? false;
                            return (
                                <div
                                    key={s.key}
                                    className={cn(
                                        "group relative flex cursor-pointer items-center gap-3 transition-colors",
                                        isMobile
                                            ? "px-3 py-3 rounded-xl"
                                            : "px-2.5 py-2 rounded-lg border",
                                        active
                                            ? isMobile
                                                ? "bg-muted text-foreground"
                                                : "border-foreground/20 bg-muted/60 text-foreground"
                                            : isMobile
                                                ? "hover:bg-muted/60"
                                                : "border-transparent hover:border-border hover:bg-muted/40"
                                    )}
                                    onClick={() => switchSession(s.key)}
                                >
                                    <div
                                        className={cn(
                                            "flex shrink-0 items-center justify-center rounded-full leading-none",
                                            isMobile ? "h-11 w-11 text-xl" : "h-6 w-6 text-sm",
                                            active ? "bg-background text-foreground ring-1 ring-border" : "bg-muted text-muted-foreground"
                                        )}
                                    >
                                        {CHANNEL_ICONS[channel] ?? "\ud83d\udcac"}
                                    </div>
                                    <div className="min-w-0 flex-1 overflow-hidden">
                                        <div className="flex items-baseline justify-between gap-1">
                                            <span
                                                className={cn(
                                                    "truncate font-medium leading-snug",
                                                    isMobile ? "text-sm" : "text-xs"
                                                )}
                                            >
                                                {label}
                                            </span>
                                            <span
                                                className={cn(
                                                    "shrink-0 text-[10px] leading-snug",
                                                    "text-muted-foreground/70"
                                                )}
                                            >
                                                {formatDate(s.updated_at)}
                                            </span>
                                        </div>
                                        <p
                                            className={cn(
                                                "mt-0.5 truncate leading-snug",
                                                isMobile ? "text-xs" : "text-[10px]",
                                                "text-muted-foreground"
                                            )}
                                        >
                                            {sessionBusy ? (
                                                <StatusDot tone="info" label="Processing..." pulse />
                                            ) : (
                                                `${channel} · ${s.last_message || s.key}`
                                            )}
                                        </p>
                                    </div>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className={cn(
                                            "shrink-0 transition-opacity",
                                            isMobile
                                                ? cn(
                                                    "h-8 w-8 opacity-0 active:opacity-100",
                                                    active && "opacity-100"
                                                )
                                                : cn(
                                                    "h-5 w-5 opacity-0 group-hover:opacity-100",
                                                    active && "opacity-100"
                                                )
                                        )}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            setDeleteKey(s.key);
                                        }}
                                    >
                                        <Trash2
                                            className={cn(isMobile ? "h-4 w-4" : "h-3 w-3")}
                                        />
                                    </Button>
                                </div>
                            );
                        })}

                        {filteredSessions.length === 0 && (
                            <div
                                className={cn(
                                    "flex flex-col items-center justify-center text-muted-foreground",
                                    isMobile ? "py-16 gap-2" : "py-6 gap-1"
                                )}
                            >
                                <MessageSquare
                                    className={cn(
                                        isMobile ? "h-10 w-10 opacity-20" : "h-6 w-6 opacity-20"
                                    )}
                                />
                                <p className={cn(isMobile ? "text-sm" : "text-xs")}>
                                    {t("common.noData")}
                                </p>
                            </div>
                        )}
                    </div>
                </div>

                {isMobile && (
                    <button
                        onClick={newChat}
                        title={t("chat.newChat")}
                        className="fixed bottom-20 right-5 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform active:scale-95 hover:bg-primary/90"
                    >
                        <Plus className="h-6 w-6" />
                    </button>
                )}
                </aside>
            )}

            {/* Chat area */}
            {!sessionsOnly && (
                <div
                className={cn(
                    "flex flex-col overflow-hidden bg-card",
                    isMobile
                        ? cn(
                            "w-full flex-1 min-h-0",
                            !mobileShowChat && "hidden"
                        )
                        : "flex-1 rounded-xl border border-border bg-card"
                )}
                style={
                    isMobile ? undefined : { boxShadow: "var(--shadow-card)" }
                }
            >
                {isMobile && (
                    <div className="flex h-12 shrink-0 items-center gap-2 px-3">
                        <Button
                            size="icon"
                            variant="ghost"
                            className="h-9 w-9"
                            onClick={() => setMobileShowChat(false)}
                        >
                            <ArrowLeft className="h-5 w-5" />
                        </Button>
                        <span className="flex-1 truncate text-sm font-medium">
                            {(() => {
                                if (!currentSessionKey) return t("nav.chat");
                                const parts = currentSessionKey.split(":");
                                const isWeb = parts[0] === "web";
                                const raw = isWeb
                                    ? (parts[2] ?? currentSessionKey)
                                    : (parts[parts.length - 1] ?? currentSessionKey);
                                return raw.length > 30 ? raw.slice(0, 30) + "..." : raw;
                            })()}
                        </span>
                    </div>
                )}
                <ChatWindow />
                </div>
            )}

            <Dialog open={!!deleteKey} onOpenChange={(open) => !open && setDeleteKey(null)}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle>{t("chat.deleteSession") ?? "Delete Session"}</DialogTitle>
                        <DialogDescription>
                            {t("chat.deleteSessionConfirm") ?? "This session and all its messages will be permanently deleted. This action cannot be undone."}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setDeleteKey(null)}>
                            {t("common.cancel") ?? "Cancel"}
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={() => {
                                if (!deleteKey) return;
                                const isActive = deleteKey === currentSessionKey;
                                if (isActive) {
                                    const idx = displaySessions.findIndex((x) => x.key === deleteKey);
                                    const next = displaySessions[idx + 1] ?? displaySessions[idx - 1];
                                    if (next) switchSession(next.key);
                                    else newChat();
                                }
                                deleteSession.mutate(deleteKey);
                                setDeleteKey(null);
                            }}
                        >
                            {t("common.delete") ?? "Delete"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
