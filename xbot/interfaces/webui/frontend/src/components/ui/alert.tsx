import * as React from "react";
import { cn } from "@/lib/utils";

const alertVariants = {
    default: "border-border bg-card text-card-foreground",
    muted: "border-border bg-muted/40 text-foreground",
    destructive: "border-destructive/40 bg-destructive/10 text-destructive",
    warning: "border-warning/40 bg-warning/10 text-foreground",
    success: "border-success/40 bg-success/10 text-foreground",
};

interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
    variant?: keyof typeof alertVariants;
}

function Alert({ className, variant = "default", ...props }: AlertProps) {
    return (
        <div
            role="alert"
            className={cn("relative w-full rounded-lg border px-4 py-3 text-sm", alertVariants[variant], className)}
            {...props}
        />
    );
}

function AlertTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
    return <h5 className={cn("mb-1 font-medium leading-none tracking-tight", className)} {...props} />;
}

function AlertDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
    return <div className={cn("text-sm leading-relaxed text-muted-foreground", className)} {...props} />;
}

export { Alert, AlertDescription, AlertTitle };
