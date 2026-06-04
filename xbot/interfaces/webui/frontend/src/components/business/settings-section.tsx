import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface SettingsSectionProps {
    title: ReactNode;
    description?: ReactNode;
    children: ReactNode;
    actions?: ReactNode;
    className?: string;
}

export function SettingsSection({ title, description, children, actions, className }: SettingsSectionProps) {
    return (
        <section className={cn("rounded-xl border bg-card", className)}>
            <div className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="space-y-1">
                    <h2 className="text-base font-semibold tracking-tight">{title}</h2>
                    {description && <p className="text-sm text-muted-foreground">{description}</p>}
                </div>
                {actions && <div className="flex items-center gap-2">{actions}</div>}
            </div>
            <div className="p-4">{children}</div>
        </section>
    );
}
