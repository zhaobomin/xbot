import { useRef, useState, useCallback, useLayoutEffect } from "react";
import { useTranslation } from "react-i18next";
import {
    Send,
    Square,
    Wifi,
    WifiOff,
    Paperclip,
    X,
    Loader2,
    ImageIcon,
    FileText,
    Terminal,
} from "lucide-react";
import { nanoid } from "nanoid";
import { toast } from "sonner";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { cn } from "../../lib/utils";
import { uploadFile } from "../../hooks/use-config";

interface Attachment {
    id: string;
    name: string;
    url?: string;
    uploading: boolean;
}

interface ChatInputProps {
    onSend: (content: string) => void;
    disabled?: boolean;
    onStop?: () => void;
    isWaiting?: boolean;
    isConnected?: boolean;
    showToolMessages?: boolean;
    onToggleToolMessages?: () => void;
    readOnly?: boolean;
}

export function ChatInput({
    onSend,
    disabled,
    onStop,
    isWaiting,
    isConnected = true,
    showToolMessages = false,
    onToggleToolMessages,
    readOnly = false,
}: ChatInputProps) {
    const { t } = useTranslation();
    const [value, setValue] = useState("");
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const isComposingRef = useRef(false);
    const compositionEndedAtRef = useRef(0);

    const MAX_TEXTAREA_H = 240;
    useLayoutEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.overflowY = "hidden";
        el.style.height = "0px";
        const contentH = el.scrollHeight;
        if (contentH > MAX_TEXTAREA_H) {
            el.style.height = MAX_TEXTAREA_H + "px";
            el.style.overflowY = "auto";
        } else {
            el.style.height = Math.max(contentH, 52) + "px";
        }
    }, [value]);

    const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20MB

    const handleFilesSelected = useCallback(
        async (files: File[]) => {
            for (const file of files) {
                if (file.size > MAX_FILE_SIZE) {
                    toast.error(
                        `${file.name} exceeds 20MB limit`
                    );
                    continue;
                }
                const id = nanoid();
                setAttachments((prev) => [
                    ...prev,
                    { id, name: file.name, uploading: true },
                ]);
                try {
                    const url = await uploadFile(file);
                    setAttachments((prev) =>
                        prev.map((a) => (a.id === id ? { ...a, url, uploading: false } : a))
                    );
                } catch (err: unknown) {
                    const detail = (
                        err as { response?: { data?: { detail?: string } } }
                    )?.response?.data?.detail;
                    toast.error(detail ?? t("chat.uploadFailed"));
                    setAttachments((prev) => prev.filter((a) => a.id !== id));
                }
            }
        },
        [t]
    );

    const handlePaste = useCallback(
        (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
            const fileItems = Array.from(e.clipboardData.items).filter(
                (i) => i.kind === "file"
            );
            if (fileItems.length === 0) return;
            e.preventDefault();
            const files = fileItems
                .map((i) => i.getAsFile())
                .filter(Boolean) as File[];
            handleFilesSelected(files);
        },
        [handleFilesSelected]
    );

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key !== "Enter" || e.shiftKey) return;

        const nativeEvent = e.nativeEvent as KeyboardEvent;
        const justEndedComposition = Date.now() - compositionEndedAtRef.current < 180;
        const isComposing =
            isComposingRef.current ||
            nativeEvent.isComposing ||
            nativeEvent.keyCode === 229 ||
            justEndedComposition;

        if (isComposing) {
            e.preventDefault();
            return;
        }

        e.preventDefault();
        handleSend();
    };

    const isUploading = attachments.some((a) => a.uploading);

    const handleSend = useCallback(() => {
        const text = value.trim();
        const readyAttachments = attachments.filter((a) => a.url && !a.uploading);
        if ((!text && readyAttachments.length === 0) || disabled || readOnly || isUploading)
            return;

        let content = text;
        for (const att of readyAttachments) {
            if (att.url) {
                const isImage = /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(att.name);
                content += `\n${isImage ? `![${att.name}](${att.url})` : `[${att.name}](${att.url})`}`;
            }
        }

        onSend(content.trim());
        setValue("");
        setAttachments([]);
        if (textareaRef.current) {
            textareaRef.current.style.height = "52px";
            textareaRef.current.style.overflowY = "hidden";
        }
    }, [value, attachments, disabled, readOnly, isUploading, onSend]);

    const removeAttachment = (id: string) =>
        setAttachments((prev) => prev.filter((a) => a.id !== id));

    const canSend =
        (value.trim().length > 0 ||
            attachments.filter((a) => a.url).length > 0) &&
        !isUploading;

    return (
        <div className="shrink-0 border-t border-border/30 bg-card/95 px-4 py-3 backdrop-blur md:px-8">
            <div className="mx-auto w-full max-w-7xl">
                <div
                    className={cn(
                        "relative flex flex-col rounded-xl border border-border/35 bg-background/70 transition-all",
                        isWaiting
                            ? "border-primary/40"
                            : "focus-within:border-primary/30 focus-within:ring-1 focus-within:ring-primary/10"
                    )}
                >
                    {attachments.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 px-4 pt-3">
                            {attachments.map((att) => {
                                const isImage = /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(
                                    att.name
                                );
                                return (
                                    <div
                                        key={att.id}
                                        className="flex items-center gap-1.5 rounded-lg border bg-muted/60 px-2.5 py-1 text-xs"
                                    >
                                        {att.uploading ? (
                                            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                                        ) : isImage ? (
                                            <ImageIcon className="h-3 w-3 text-primary" />
                                        ) : (
                                            <FileText className="h-3 w-3 text-primary" />
                                        )}
                                        <span className="max-w-[140px] truncate text-muted-foreground">
                                            {att.uploading ? t("chat.uploading") : att.name}
                                        </span>
                                        {!att.uploading && (
                                            <button
                                                onClick={() => removeAttachment(att.id)}
                                                className="ml-0.5 rounded-sm text-muted-foreground hover:text-foreground"
                                            >
                                                <X className="h-3 w-3" />
                                            </button>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    <Textarea
                        ref={textareaRef}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        onKeyDown={handleKeyDown}
                        onCompositionStart={() => {
                            isComposingRef.current = true;
                        }}
                        onCompositionEnd={() => {
                            isComposingRef.current = false;
                            compositionEndedAtRef.current = Date.now();
                        }}
                        onPaste={handlePaste}
                        placeholder={t("chat.placeholder")}
                        rows={1}
                        className="w-full resize-none border-0 bg-transparent px-4 py-3 shadow-none focus-visible:ring-0 text-base leading-relaxed"
                        disabled={readOnly || (!isWaiting && disabled)}
                    />
                    <div className="flex items-center justify-between px-3 pb-2">
                        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            {isConnected ? (
                                <Wifi className="h-3 w-3 text-success" />
                            ) : (
                                <WifiOff className="h-3 w-3 text-destructive" />
                            )}
                            <span>
                                {isConnected ? t("chat.connected") : t("chat.disconnected")}
                            </span>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 ml-1"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={isWaiting || readOnly}
                                title={t("chat.uploadAttachment")}
                            >
                                <Paperclip className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                className={cn(
                                    "h-6 w-6 transition-colors",
                                    showToolMessages
                                        ? "text-primary"
                                        : "text-muted-foreground/40 hover:text-muted-foreground"
                                )}
                                onClick={onToggleToolMessages}
                                title={
                                    showToolMessages
                                        ? t("chat.hideToolMessages")
                                        : t("chat.showToolMessages")
                                }
                            >
                                <Terminal className="h-3.5 w-3.5" />
                            </Button>
                            <input
                                ref={fileInputRef}
                                type="file"
                                multiple
                                hidden
                                accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.md,.json,.py,.js,.ts,.jsx,.tsx,.html,.css,.zip,.tar,.gz"
                                onChange={(e) => {
                                    if (e.target.files)
                                        handleFilesSelected(Array.from(e.target.files));
                                    e.target.value = "";
                                }}
                            />
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">
                                {readOnly ? "只读" : isWaiting ? "" : t("chat.hint")}
                            </span>
                            {isWaiting ? (
                                <Button
                                    size="sm"
                                    variant="destructive"
                                    onClick={onStop}
                                    className="h-8 gap-1.5 rounded-xl px-3"
                                >
                                    <Square className="h-3.5 w-3.5" />
                                    {t("chat.stop")}
                                </Button>
                            ) : (
                                <Button
                                    size="sm"
                                    onClick={handleSend}
                                    disabled={!canSend || disabled || readOnly}
                                    className="h-8 gap-1.5 rounded-xl px-3"
                                >
                                    <Send className="h-3.5 w-3.5" />
                                    {t("chat.send")}
                                </Button>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
