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
                variant === "info" && "border-border/80 bg-card/80",
                variant === "success" && "border-success/20 bg-success/10 dark:border-success/20 dark:bg-success/10",
                variant === "warning" && "border-warning/20 bg-warning/10 dark:border-warning/20 dark:bg-warning/10",
                variant === "empty" && "border-dashed border-border/80 bg-secondary/20"
            )}
        >
            <div className="p-4 sm:p-5">
                <div className="flex items-start gap-4">
                    <div
                        aria-hidden="true"
                        className={cn(
                            "mt-0.5 h-10 w-1 shrink-0 rounded-full",
                            variant === "info" && "bg-primary/80",
                            variant === "success" && "bg-success",
                            variant === "warning" && "bg-warning",
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
