import { cn } from "../../lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

interface ListCardProps {
    title: string;
    subtitle?: string;
    icon?: React.ReactNode;
    badge?: React.ReactNode;
    actions?: React.ReactNode;
    children?: React.ReactNode;
    className?: string;
    onClick?: () => void;
}

export function ListCard({
    title,
    subtitle,
    icon,
    badge,
    actions,
    children,
    className,
    onClick,
}: ListCardProps) {
    return (
        <Card
            className={cn(
                "transition-shadow hover:shadow-panel",
                onClick && "cursor-pointer",
                className
            )}
            onClick={onClick}
        >
            <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-2">
                {icon && (
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        {icon}
                    </div>
                )}
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        <CardTitle className="truncate text-sm font-semibold">{title}</CardTitle>
                        {badge}
                    </div>
                    {subtitle && (
                        <p className="mt-0.5 text-xs text-muted-foreground truncate">{subtitle}</p>
                    )}
                </div>
                {actions && <div className="shrink-0 flex items-center gap-1">{actions}</div>}
            </CardHeader>
            {children && <CardContent className="pt-0">{children}</CardContent>}
        </Card>
    );
}
