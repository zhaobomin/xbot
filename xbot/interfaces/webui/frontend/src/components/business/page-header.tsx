import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
    title: ReactNode;
    description?: ReactNode;
    actions?: ReactNode;
    className?: string;
}

export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
    return (
        <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between", className)}>
            <div className="min-w-0 space-y-1">
                <h1 className="truncate text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
                {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
            </div>
            {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
    );
}
