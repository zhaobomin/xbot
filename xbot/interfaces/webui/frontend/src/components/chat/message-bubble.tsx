import { useState, useCallback, useRef, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { cn } from "../../lib/utils";
import type { ChatMessage } from "../../stores/chat-store";
import { ToolCallCard } from "./tool-call-card";
import { ThinkingBlock } from "./thinking-block";
import { useAuthStore } from "../../stores/auth-store";
import {
    Info,
    ChevronDown,
    ChevronRight,
    CheckCircle2,
    XCircle,
    Bot,
    Copy,
    Check,
    Undo2,
} from "lucide-react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "../ui/dialog";
import { Button } from "../ui/button";

interface MessageBubbleProps {
    message: ChatMessage;
    onRevoke?: (messageId: string) => void;
}

function splitThinking(
    content: string
): { type: "text" | "thinking"; content: string }[] {
    const parts: { type: "text" | "thinking"; content: string }[] = [];
    const regex = /<think>([\s\S]*?)<\/think>/g;
    let lastIndex = 0;
    let match;
    while ((match = regex.exec(content)) !== null) {
        if (match.index > lastIndex) {
            parts.push({ type: "text", content: content.slice(lastIndex, match.index) });
        }
        parts.push({ type: "thinking", content: match[1] });
        lastIndex = regex.lastIndex;
    }
    if (lastIndex < content.length) {
        parts.push({ type: "text", content: content.slice(lastIndex) });
    }
    return parts;
}

function stripThinkTags(content: string): string {
    return content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}

function CodeBlock({ children, className }: { children: ReactNode; className?: string }) {
    const [copied, setCopied] = useState(false);
    const preRef = useRef<HTMLPreElement>(null);
    const lang = className?.replace(/^language-/, "") ?? "";

    const handleCopy = useCallback(() => {
        const text = preRef.current?.textContent ?? "";
        navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        });
    }, []);

    return (
        <div className="group/code relative">
            {lang && (
                <div className="flex items-center justify-between rounded-t-xl bg-zinc-200/80 dark:bg-zinc-800/80 px-4 py-1.5 text-xs font-medium text-muted-foreground">
                    <span>{lang}</span>
                    <button
                        onClick={handleCopy}
                        className="flex items-center gap-1 text-muted-foreground/60 hover:text-foreground transition-colors"
                    >
                        {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
                        <span>{copied ? "Copied" : "Copy"}</span>
                    </button>
                </div>
            )}
            <pre ref={preRef} className={cn("overflow-x-auto", lang && "!rounded-t-none !mt-0")}>
                <code className={className}>{children}</code>
            </pre>
            {!lang && (
                <button
                    onClick={handleCopy}
                    className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-zinc-200/80 dark:bg-zinc-700/80 px-2 py-1 text-[10px] text-muted-foreground opacity-0 group-hover/code:opacity-100 transition-opacity"
                >
                    {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
                </button>
            )}
        </div>
    );
}

function isExternalHref(href: string): boolean {
    try {
        const base = typeof window !== "undefined" ? window.location.href : "http://localhost";
        const url = new URL(href, base);
        return ["http:", "https:", "mailto:", "tel:"].includes(url.protocol);
    } catch {
        return false;
    }
}

function safeMarkdownHref(href?: string): string | undefined {
    if (!href) return undefined;
    if (href.startsWith("#") || href.startsWith("/") || href.startsWith("./") || href.startsWith("../")) {
        return href;
    }
    return isExternalHref(href) ? href : undefined;
}

function handleMarkdownLinkClick(event: React.MouseEvent<HTMLAnchorElement>, href: string) {
    if (!isExternalHref(href)) return;
    const opened = window.open(href, "_blank", "noopener,noreferrer");
    if (opened) {
        event.preventDefault();
    }
}

function MarkdownContent({
    content,
    className,
}: {
    content: string;
    className?: string;
}) {
    return (
        <div
            className={cn(
                "prose prose-sm max-w-none dark:prose-invert break-words",
                "[&_p]:leading-relaxed [&_p]:my-1 [&_p]:[overflow-wrap:anywhere]",
                "[&_pre]:rounded-xl [&_pre]:bg-zinc-100 dark:[&_pre]:bg-zinc-900 [&_pre]:text-zinc-900 dark:[&_pre]:text-zinc-100 [&_pre]:p-4 [&_pre]:text-xs [&_pre]:ring-1 [&_pre]:ring-border/50 [&_pre]:overflow-x-auto",
                "[&_code:not(pre_code)]:rounded [&_code:not(pre_code)]:bg-muted [&_code:not(pre_code)]:px-1.5 [&_code:not(pre_code)]:py-0.5 [&_code:not(pre_code)]:text-xs [&_code:not(pre_code)]:font-mono [&_code:not(pre_code)]:text-foreground",
                "[&_blockquote]:border-l-2 [&_blockquote]:border-l-border [&_blockquote]:pl-4 [&_blockquote]:italic [&_blockquote]:text-muted-foreground",
                "[&_th]:bg-muted/60 [&_th]:px-3 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium [&_td]:px-3 [&_td]:py-1.5 [&_td]:border-t [&_td]:border-border/40 [&_tr:nth-child(even)]:bg-muted/20",
                "[&_ul]:my-2 [&_ol]:my-2 [&_li]:my-0.5",
                "[&_a]:text-primary [&_a]:no-underline hover:[&_a]:underline",
                className
            )}
        >
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                    a({ href, children }) {
                        const safeHref = safeMarkdownHref(href);
                        if (!safeHref) {
                            return <span>{children}</span>;
                        }
                        const external = isExternalHref(safeHref);
                        return (
                            <a
                                href={safeHref}
                                target={external ? "_blank" : undefined}
                                rel={external ? "noopener noreferrer" : undefined}
                                onClick={external ? (event) => handleMarkdownLinkClick(event, safeHref) : undefined}
                            >
                                {children}
                            </a>
                        );
                    },
                    pre({ children }) {
                        const child = children as React.ReactElement<{ className?: string; children?: ReactNode }>;
                        const codeProps = child?.props;
                        return (
                            <CodeBlock className={codeProps?.className}>
                                {codeProps?.children}
                            </CodeBlock>
                        );
                    },
                    table({ children }) {
                        return (
                            <div className="my-3 overflow-x-auto rounded-lg border border-border/50">
                                <table className="m-0 w-full min-w-max text-xs">
                                    {children}
                                </table>
                            </div>
                        );
                    },
                }}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
}

function SubAgentProgressBlock({ message }: { message: ChatMessage }) {
    const isError = message.content.startsWith("Error:");
    const match = message.content.match(/^\[↳ (.+?)\] (.+)$/);
    const label = match?.[1] ?? "SubAgent";
    const hint = match?.[2] ?? message.content;
    const isLong = hint.length > 300;
    const [open, setOpen] = useState(false);

    return (
        <div
            className={cn(
                "rounded-lg border text-xs overflow-hidden",
                isError
                    ? "border-destructive/40 bg-destructive/10"
                    : "border-border bg-muted/30"
            )}
        >
            <button
                onClick={() => isLong && setOpen((v) => !v)}
                className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 text-left rounded-lg transition-colors",
                    isLong && "hover:bg-muted cursor-pointer",
                    !isLong && "cursor-default"
                )}
            >
                <Bot className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="font-medium text-muted-foreground truncate max-w-[80px]">
                    {label}
                </span>
                <span className="text-muted-foreground/50">·</span>
                {isError ? (
                    <XCircle className="h-3 w-3 shrink-0 text-destructive" />
                ) : (
                    <CheckCircle2 className="h-3 w-3 shrink-0 text-success" />
                )}
                <span className="font-mono font-medium text-foreground/70 truncate">
                    {hint}
                </span>
                <span className="ml-auto mr-1 shrink-0 text-[10px] text-muted-foreground/40">
                    {new Date(message.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                    })}
                </span>
                {isLong &&
                    (open ? (
                        <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    ) : (
                        <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    ))}
            </button>
            {(open || !isLong) && hint.length > 80 && (
                <div className="border-t px-3 py-2">
                    <pre
                        className={cn(
                            "max-h-48 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed",
                            isError
                                ? "text-destructive"
                                : "text-muted-foreground/80"
                        )}
                    >
                        {hint}
                    </pre>
                </div>
            )}
        </div>
    );
}

function ToolResultBlock({ message }: { message: ChatMessage }) {
    const isError = message.content.startsWith("Error:");
    const isLong = message.content.length > 300;
    const [open, setOpen] = useState(false);

    return (
        <div
            className={cn(
                "rounded-lg border text-xs overflow-hidden",
                isError
                    ? "border-destructive/40 bg-destructive/10"
                    : "border-border/60 bg-muted/30 dark:bg-muted/20"
            )}
        >
            <button
                onClick={() => isLong && setOpen((v) => !v)}
                className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 text-left rounded-lg transition-colors",
                    isLong && "hover:bg-muted/50 cursor-pointer",
                    !isLong && "cursor-default"
                )}
            >
                {isError ? (
                    <XCircle className="h-3 w-3 shrink-0 text-destructive" />
                ) : (
                    <CheckCircle2 className="h-3 w-3 shrink-0 text-success" />
                )}
                <span className="font-mono font-medium text-foreground/70 truncate">
                    {message.name || "tool"}
                </span>
                <span className="ml-auto mr-1 shrink-0 text-[10px] text-muted-foreground/40">
                    {new Date(message.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                    })}
                </span>
                {isLong &&
                    (open ? (
                        <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    ) : (
                        <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    ))}
            </button>
            {(open || !isLong) && (
                <div className="border-t border-border/40 px-3 py-2">
                    <pre
                        className={cn(
                            "max-h-48 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed",
                            isError
                                ? "text-destructive"
                                : "text-muted-foreground/80"
                        )}
                    >
                        {message.content}
                    </pre>
                </div>
            )}
        </div>
    );
}

function SubAgentToolBlock({ message }: { message: ChatMessage }) {
    const isError = message.content.startsWith("Error:");
    const isSummary = /^\[Sub[Aa]gent[\s']/.test(message.content);
    let displayContent = message.content;
    let resultSnippet = "";
    if (isSummary) {
        const resultMatch = message.content.match(/\nResult:\s*([\s\S]*)/);
        resultSnippet = resultMatch?.[1]?.trim() ?? "";
        displayContent = resultSnippet || message.content;
    }
    let label = message.name || "";
    if (!label && isSummary) {
        const labelMatch = message.content.match(/^\[Subagent '(.+?)'/);
        label = labelMatch?.[1] ?? "SubAgent";
    }
    if (!label) label = "SubAgent";
    const isLong = displayContent.length > 300;
    const [open, setOpen] = useState(false);

    return (
        <div
            className={cn(
                "rounded-lg border text-xs overflow-hidden",
                isError
                    ? "border-destructive/40 bg-destructive/10"
                    : "border-border bg-muted/30"
            )}
        >
            <button
                onClick={() => isLong && setOpen((v) => !v)}
                className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 text-left rounded-lg transition-colors",
                    isLong && "hover:bg-muted cursor-pointer",
                    !isLong && "cursor-default"
                )}
            >
                <Bot className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="font-medium text-muted-foreground truncate max-w-[120px]">
                    ⤹ {label}
                </span>
                <span className="text-muted-foreground/40">·</span>
                {isError ? (
                    <XCircle className="h-3 w-3 shrink-0 text-destructive" />
                ) : (
                    <CheckCircle2 className="h-3 w-3 shrink-0 text-success" />
                )}
                <span className="font-mono font-medium text-foreground/70 truncate">
                    {isSummary
                        ? resultSnippet.length > 60
                            ? resultSnippet.slice(0, 60) + "..."
                            : resultSnippet || "completed"
                        : message.name || "tool"}
                </span>
                <span className="ml-auto mr-1 shrink-0 text-[10px] text-muted-foreground/40">
                    {new Date(message.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                    })}
                </span>
                {isLong &&
                    (open ? (
                        <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    ) : (
                        <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                    ))}
            </button>
            {(open || !isLong) && displayContent.length > 60 && (
                <div className="border-t px-3 py-2">
                    <pre
                        className={cn(
                            "max-h-48 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed",
                            isError
                                ? "text-destructive"
                                : "text-muted-foreground/80"
                        )}
                    >
                        {displayContent}
                    </pre>
                </div>
            )}
        </div>
    );
}

function SystemMessageBlock({ message }: { message: ChatMessage }) {
    const [open, setOpen] = useState(false);

    return (
        <div className="rounded border border-dashed border-muted-foreground/20 bg-muted/20 text-xs">
            <button
                onClick={() => setOpen((v) => !v)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/40 rounded transition-colors"
            >
                <Info className="h-3 w-3 shrink-0 text-muted-foreground/60" />
                <span className="font-medium text-muted-foreground/70">System</span>
                <span className="ml-auto mr-1 shrink-0 text-[10px] text-muted-foreground/40">
                    {new Date(message.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                    })}
                </span>
                {open ? (
                    <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                ) : (
                    <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/50" />
                )}
            </button>
            {open && (
                <div className="border-t border-muted-foreground/10 px-3 py-2">
                    <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed text-muted-foreground/60">
                        {message.content}
                    </pre>
                </div>
            )}
        </div>
    );
}

function ToolMessageWrapper({ children }: { children: React.ReactNode }) {
    return (
        <div className="flex gap-3 px-4">
            <div className="h-8 w-8 shrink-0" />
            <div className="flex-1 min-w-0">{children}</div>
        </div>
    );
}

export function MessageBubble({ message, onRevoke }: MessageBubbleProps) {
    const user = useAuthStore((s) => s.user);
    const [revokeDialogOpen, setRevokeDialogOpen] = useState(false);

    if (
        !message.content?.trim() &&
        !message.toolCalls?.length &&
        !message.isStreaming
    ) {
        return null;
    }

    if (message.role === "tool" && message.name === "message") {
        return null;
    }

    if (message.role === "sub_tool") {
        return (
            <ToolMessageWrapper>
                <SubAgentToolBlock message={message} />
            </ToolMessageWrapper>
        );
    }

    if (message.role === "tool" && message.isSubAgent) {
        return (
            <ToolMessageWrapper>
                <SubAgentProgressBlock message={message} />
            </ToolMessageWrapper>
        );
    }

    if (message.role === "tool") {
        return (
            <ToolMessageWrapper>
                <ToolResultBlock message={message} />
            </ToolMessageWrapper>
        );
    }

    if (message.role === "system") {
        return (
            <ToolMessageWrapper>
                <SystemMessageBlock message={message} />
            </ToolMessageWrapper>
        );
    }

    const isUser = message.role === "user";
    const parts = splitThinking(message.content ?? "");
    const [copied, setCopied] = useState(false);

    const copyContent = () => {
        const text = stripThinkTags(message.content ?? "");
        navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        });
    };

    const handleRevokeConfirm = () => {
        setRevokeDialogOpen(false);
        onRevoke?.(message.id);
    };

    return (
        <>
            <div
                className={cn(
                    "group flex gap-3 px-4",
                    isUser ? "flex-row-reverse" : "flex-row"
                )}
            >
                {/* Avatar */}
                <div
                    className={cn(
                        "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold select-none",
                        isUser
                            ? "bg-foreground text-background"
                            : "bg-muted text-foreground/70"
                    )}
                >
                    {isUser ? user?.username?.[0]?.toUpperCase() ?? "U" : "x"}
                </div>

                {/* Content */}
                <div
                    className={cn(
                        "flex min-w-0 flex-col gap-1",
                        isUser ? "max-w-2xl items-end" : "flex-1 items-start"
                    )}
                >
                    {isUser ? (
                        <div className="rounded-xl rounded-tr-sm border bg-muted px-4 py-2.5 text-sm leading-relaxed text-foreground">
                            <MarkdownContent
                                content={message.content ?? ""}
                                className="prose-p:my-0 prose-ul:my-1 prose-ol:my-1"
                            />
                        </div>
                    ) : (
                        <div className="w-full min-w-0 space-y-2 [overflow-wrap:anywhere]">
                            {parts.map((part, i) =>
                                part.type === "thinking" ? (
                                    <ThinkingBlock key={i} content={part.content} />
                                ) : part.content.trim() ? (
                                    <MarkdownContent key={i} content={part.content} />
                                ) : null
                            )}
                            {message.toolCalls?.map((tool) => (
                                <ToolCallCard key={tool.id} tool={tool} />
                            ))}
                            {message.isStreaming && (
                                <span className="inline-block h-4 w-0.5 animate-pulse rounded-full bg-foreground/60 align-middle ml-0.5" />
                            )}
                        </div>
                    )}
                    <div className="flex items-center gap-1 px-1">
                        <span className="text-xs text-muted-foreground/60">
                            {new Date(message.timestamp).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                            })}
                        </span>
                        {!message.isStreaming && (
                            <button
                                onClick={copyContent}
                                className="md:opacity-0 md:group-hover:opacity-100 transition-opacity text-muted-foreground/50 hover:text-muted-foreground p-0.5 rounded"
                                aria-label="Copy message"
                            >
                                {copied ? (
                                    <Check className="h-3 w-3 text-success" />
                                ) : (
                                    <Copy className="h-3 w-3" />
                                )}
                            </button>
                        )}
                        {!message.isStreaming && onRevoke && (
                            <button
                                onClick={() => setRevokeDialogOpen(true)}
                                className="md:opacity-0 md:group-hover:opacity-100 transition-opacity text-muted-foreground/50 hover:text-red-500 p-0.5 rounded"
                                aria-label="Revoke message"
                            >
                                <Undo2 className="h-3 w-3" />
                            </button>
                        )}
                    </div>
                </div>
            </div>

            <Dialog open={revokeDialogOpen} onOpenChange={setRevokeDialogOpen}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle>Revoke Message</DialogTitle>
                        <DialogDescription>
                            {isUser
                                ? "This will revoke this message and all subsequent replies. This action cannot be undone."
                                : "This will revoke this message. This action cannot be undone."}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setRevokeDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleRevokeConfirm}>
                            Revoke
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
