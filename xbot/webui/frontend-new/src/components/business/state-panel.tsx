import { cn } from "../../lib/utils";

export function StatePanel({
    title,
    description,
    variant = "info",
}: {
    title: string;
    description: string;
    variant?: "info" | "success" | "warning" | "empty";
}) {
    return (
        <div
            className={cn(
                "overflow-hidden rounded-2xl border shadow-none",
                variant === "info" && "border-border/80 bg-card/78",
                variant === "success" && "border-emerald-200/80 bg-emerald-50/78 dark:border-emerald-800/60 dark:bg-emerald-950/30",
                variant === "warning" && "border-amber-200/80 bg-amber-50/78 dark:border-amber-800/60 dark:bg-amber-950/30",
                variant === "empty" && "border-dashed border-border/80 bg-secondary/28"
            )}
        >
            <div className="p-4 sm:p-5">
                <div className="flex items-start gap-4">
                    <div
                        aria-hidden="true"
                        className={cn(
                            "mt-0.5 h-10 w-1 shrink-0 rounded-full",
                            variant === "info" && "bg-primary/80",
                            variant === "success" && "bg-emerald-400/90",
                            variant === "warning" && "bg-amber-400/90",
                            variant === "empty" && "bg-accent-foreground/30"
                        )}
                    />
                    <div className="min-w-0">
                        <div className="text-sm font-semibold">{title}</div>
                        <div className="mt-1 text-sm leading-6 text-muted-foreground">{description}</div>
                    </div>
                </div>
            </div>
        </div>
    );
}
