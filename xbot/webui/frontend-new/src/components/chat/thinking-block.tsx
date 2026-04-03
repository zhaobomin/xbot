import { useState } from "react";
import { ChevronDown, ChevronRight, Brain } from "lucide-react";

interface ThinkingBlockProps {
    content: string;
}

export function ThinkingBlock({ content }: ThinkingBlockProps) {
    const [open, setOpen] = useState(false);

    return (
        <div className="my-2 rounded-md border border-warning/20 bg-warning/5 text-xs">
            <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-warning hover:text-warning/80"
                onClick={() => setOpen((v) => !v)}
            >
                <Brain className="h-3 w-3 shrink-0" />
                <span className="font-medium">Thinking</span>
                {open ? (
                    <ChevronDown className="ml-auto h-3 w-3" />
                ) : (
                    <ChevronRight className="ml-auto h-3 w-3" />
                )}
            </button>
            {open && (
                <div className="border-t border-warning/20 px-3 py-2">
                    <pre className="whitespace-pre-wrap break-all font-mono text-xs text-warning/80">
                        {content}
                    </pre>
                </div>
            )}
        </div>
    );
}
