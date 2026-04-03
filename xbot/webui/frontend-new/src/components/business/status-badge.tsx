import { useTranslation } from "react-i18next";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";

interface StatusBadgeProps {
    running: boolean;
    error?: string | null;
    className?: string;
}

export function StatusBadge({ running, error, className }: StatusBadgeProps) {
    const { t } = useTranslation();
    if (error) {
        return (
            <Badge variant="destructive" className={cn(className)}>
                {t("channels.error")}
            </Badge>
        );
    }
    return (
        <Badge
            variant={running ? "default" : "secondary"}
            className={cn(
                running ? "bg-success hover:bg-success/90 text-success-foreground" : "",
                className
            )}
        >
            {running ? t("channels.running") : t("channels.stopped")}
        </Badge>
    );
}
