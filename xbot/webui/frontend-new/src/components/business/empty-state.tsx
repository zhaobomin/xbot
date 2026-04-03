import { cn } from "../../lib/utils";

interface EmptyStateProps {
    icon?: React.ElementType;
    title: string;
    description?: string;
    action?: React.ReactNode;
    className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
    return (
        <div className={cn("rounded-xl border border-dashed border-border bg-secondary/30 py-12 px-8 text-center", className)}>
            {Icon && (
                <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-muted/80">
                    <Icon className="h-5 w-5 text-muted-foreground" />
                </div>
            )}
            <h3 className="text-sm font-medium text-foreground">{title}</h3>
            {description && (
                <p className="mt-1 text-sm text-muted-foreground">{description}</p>
            )}
            {action && <div className="mt-4">{action}</div>}
        </div>
    );
}
