import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useSessions, useDeleteSession } from "../../hooks/use-sessions";
import { useAuthStore } from "../../stores/auth-store";
import { useChatStore } from "../../stores/chat-store";
import { cn, formatDate } from "../../lib/utils";
import { getChannelIcon } from "../../lib/channel-icons";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../ui/dialog";
import { MessageSquare, Plus, Trash2 } from "lucide-react";
import { StatusDot } from "../business/status-dot";

/**
 * SessionList — a compact, embeddable session list for the sidebar.
 * Clicking a session navigates to /chat and switches the active session.
 */
export function SessionList() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const user = useAuthStore((s) => s.user);
    const { currentSessionKey, setCurrentSession, sessionStates } = useChatStore();
    const { data: sessions } = useSessions();
    const deleteSession = useDeleteSession();

    const [deleteKey, setDeleteKey] = useState<string | null>(null);

    const isAdmin = user?.role === "admin";
    const myPrefix = `web:${user?.id}:`;

    const mySessions = useMemo(
        () =>
            isAdmin
                ? (sessions ?? []).slice().sort((a, b) =>
                    (b.updated_at ?? "").localeCompare(a.updated_at ?? "")
                )
                : (sessions?.filter((s) => s.key.startsWith(myPrefix)) ?? []),
        [isAdmin, myPrefix, sessions]
    );

    useEffect(() => {
        if (mySessions.length === 0) return;
        const keyExists = currentSessionKey && mySessions.some((s) => s.key === currentSessionKey);
        if (!keyExists && !currentSessionKey?.startsWith(myPrefix)) {
            setCurrentSession(mySessions[0].key);
        }
    }, [mySessions, currentSessionKey, setCurrentSession, myPrefix]);

    const displaySessions = useMemo(() => {
        const isLocalNew =
            currentSessionKey?.startsWith(myPrefix) && !mySessions.some((s) => s.key === currentSessionKey);
        if (isLocalNew && currentSessionKey) {
            return [
                { key: currentSessionKey, updated_at: new Date().toISOString(), last_message: undefined },
                ...mySessions,
            ];
        }
        return mySessions;
    }, [currentSessionKey, myPrefix, mySessions]);

    const newChat = () => {
        const hexId = Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) =>
            b.toString(16).padStart(2, "0")
        ).join("");
        const key = `web:${user?.id}:${hexId}`;
        setCurrentSession(key);
        navigate("/chat");
    };

    const switchSession = (key: string) => {
        setCurrentSession(key);
        navigate("/chat");
    };

    return (
        <div className="flex flex-col flex-1 min-h-0">
            {/* Session items */}
            <div className="flex-1 min-h-0 overflow-y-auto space-y-0.5 px-1">
                {displaySessions.map((s) => {
                    const isWeb = s.key.split(":")[0] === "web";
                    const parts = s.key.split(":");
                    const rawLabel = isWeb ? (parts[2] ?? s.key) : (parts[parts.length - 1] ?? s.key);
                    const label = rawLabel.length > 14 ? rawLabel.slice(0, 14) + "..." : rawLabel;
                    const active = s.key === currentSessionKey;
                    const sessionBusy = sessionStates[s.key]?.isWaiting ?? false;
                    const ChannelIcon = getChannelIcon(s.key);

                    return (
                        <div
                            key={s.key}
                            className={cn(
                                "group relative flex cursor-pointer items-center gap-2 transition-colors px-2.5 py-1.5 rounded-lg border",
                                active
                                    ? "border-foreground/20 bg-muted/60 text-foreground"
                                    : "border-transparent hover:border-border hover:bg-muted/40"
                            )}
                            onClick={() => switchSession(s.key)}
                        >
                            <div className={cn(
                                "flex shrink-0 items-center justify-center rounded-full",
                                "h-6 w-6",
                                active ? "bg-background text-foreground ring-1 ring-border" : "bg-muted text-muted-foreground"
                            )}>
                                <ChannelIcon className="h-3.5 w-3.5" />
                            </div>
                            <div className="min-w-0 flex-1 overflow-hidden">
                                <div className="flex items-baseline justify-between gap-1">
                                    <span className="truncate font-medium leading-snug text-xs">{label}</span>
                                    <span className="shrink-0 text-[10px] leading-snug text-muted-foreground/70">
                                        {formatDate(s.updated_at)}
                                    </span>
                                </div>
                                <p className="mt-0.5 truncate leading-snug text-[10px] text-muted-foreground">
                                    {sessionBusy ? (
                                        <StatusDot tone="info" label="Processing..." pulse />
                                    ) : (
                                        s.last_message || "—"
                                    )}
                                </p>
                            </div>
                            <Button
                                size="icon"
                                variant="ghost"
                                className="shrink-0 h-5 w-5 opacity-0 group-hover:opacity-100"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    setDeleteKey(s.key);
                                }}
                            >
                                <Trash2 className="h-3 w-3" />
                            </Button>
                        </div>
                    );
                })}

                {displaySessions.length === 0 && (
                    <div className="flex flex-col items-center justify-center text-muted-foreground py-4 gap-1">
                        <MessageSquare className="h-5 w-5 opacity-20" />
                        <p className="text-xs">{t("common.noData")}</p>
                    </div>
                )}
            </div>

            {/* New chat button */}
            <div className="shrink-0 px-2 py-1">
                <button
                    onClick={newChat}
                    title={t("chat.newChat")}
                    className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs font-medium transition-colors text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                >
                    <Plus className="h-3.5 w-3.5" />
                    {t("chat.newChat")}
                </button>
            </div>

            {/* Delete dialog */}
            <Dialog open={!!deleteKey} onOpenChange={(open) => !open && setDeleteKey(null)}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle>{t("chat.deleteSession") ?? "Delete Session"}</DialogTitle>
                        <DialogDescription>
                            {t("chat.deleteConfirm") ?? "This session and all its messages will be permanently deleted."}
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
                                const idx = displaySessions.findIndex((x) => x.key === deleteKey);
                                const next = displaySessions[idx + 1] ?? displaySessions[idx - 1];
                                if (next) switchSession(next.key);
                                else newChat();
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
