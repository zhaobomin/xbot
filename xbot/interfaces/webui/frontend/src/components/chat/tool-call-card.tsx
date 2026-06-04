import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolCallInfo } from "../../stores/chat-store";

interface ToolCallCardProps {
    tool: ToolCallInfo;
}

export function ToolCallCard({ tool }: ToolCallCardProps) {
    const [open, setOpen] = useState(false);

    return (
        <div className="rounded-md border bg-muted/30 text-xs">
            <button
                className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left transition-colors hover:bg-muted"
                onClick={() => setOpen((v) => !v)}
            >
                <Wrench className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="font-mono font-medium text-foreground/80">
                    {tool.name}
                </span>
                {open ? (
                    <ChevronDown className="ml-auto h-3 w-3 text-muted-foreground/60" />
                ) : (
                    <ChevronRight className="ml-auto h-3 w-3 text-muted-foreground/60" />
                )}
            </button>
            {open && (
                <div className="space-y-2 border-t px-3 py-2">
                    {tool.input && (
                        <div>
                            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                Input
                            </div>
                            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground">
                                {tool.input}
                            </pre>
                        </div>
                    )}
                    {tool.output && (
                        <div>
                            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                Output
                            </div>
                            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground">
                                {tool.output}
                            </pre>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
