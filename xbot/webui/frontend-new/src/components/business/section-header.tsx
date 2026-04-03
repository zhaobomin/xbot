import { cn } from "../../lib/utils";

interface SectionHeaderProps {
    eyebrow?: string;
    title: string;
    description?: string;
    actions?: React.ReactNode;
    className?: string;
}

export function SectionHeader({ eyebrow, title, description, actions, className }: SectionHeaderProps) {
    return (
        <div className={cn("mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between", className)}>
            <div className="min-w-0">
                {eyebrow && (
                    <div className="mb-1.5 text-xs font-medium text-primary/70">{eyebrow}</div>
                )}
                <h1 className="text-xl font-semibold text-foreground">{title}</h1>
                {description && (
                    <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>
                )}
            </div>
            {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </div>
    );
}
