export type StatusTone = "success" | "warning" | "danger" | "muted" | "info";

export function statusToneFromValue(value?: string | boolean | null): StatusTone {
    if (value === true) return "success";
    if (value === false || value == null) return "muted";
    const normalized = value.toLowerCase();
    if (["running", "online", "enabled", "active", "success", "healthy"].includes(normalized)) {
        return "success";
    }
    if (["error", "failed", "offline", "danger"].includes(normalized)) {
        return "danger";
    }
    if (["warning", "pending", "degraded"].includes(normalized)) {
        return "warning";
    }
    return "muted";
}

export const statusToneClasses: Record<StatusTone, string> = {
    success: "bg-success text-success-foreground border-success/20",
    warning: "bg-warning text-warning-foreground border-warning/20",
    danger: "bg-destructive text-destructive-foreground border-destructive/20",
    muted: "bg-muted text-muted-foreground border-border",
    info: "bg-secondary text-secondary-foreground border-border",
};

export const statusDotClasses: Record<StatusTone, string> = {
    success: "bg-success",
    warning: "bg-warning",
    danger: "bg-destructive",
    muted: "bg-muted-foreground/50",
    info: "bg-primary",
};
