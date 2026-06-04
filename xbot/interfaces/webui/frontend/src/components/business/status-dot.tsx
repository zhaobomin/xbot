import { cn } from "@/lib/utils";
import { statusDotClasses, type StatusTone } from "@/lib/status";

interface StatusDotProps {
    tone?: StatusTone;
    label?: string;
    pulse?: boolean;
    className?: string;
}

export function StatusDot({ tone = "muted", label, pulse, className }: StatusDotProps) {
    return (
        <span className={cn("inline-flex items-center gap-2 text-sm", className)}>
            <span className={cn("h-2 w-2 rounded-full", statusDotClasses[tone], pulse && "animate-pulse")} />
            {label && <span className="text-muted-foreground">{label}</span>}
        </span>
    );
}
