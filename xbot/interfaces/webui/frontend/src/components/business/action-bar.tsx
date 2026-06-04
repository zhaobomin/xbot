import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface ActionBarProps {
    children: ReactNode;
    className?: string;
}

export function ActionBar({ children, className }: ActionBarProps) {
    return (
        <div className={cn("flex flex-col gap-2 rounded-lg border bg-card p-3 sm:flex-row sm:items-center sm:justify-between", className)}>
            {children}
        </div>
    );
}
