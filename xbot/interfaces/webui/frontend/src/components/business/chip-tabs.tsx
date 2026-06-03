import { cn } from "../../lib/utils";

interface ChipTabsProps<T extends string> {
    value: T;
    onChange: (value: T) => void;
    items: { value: T; label: string; count?: number }[];
    className?: string;
}

export function ChipTabs<T extends string>({
    value,
    onChange,
    items,
    className,
}: ChipTabsProps<T>) {
    return (
        <div className={cn("flex flex-wrap gap-1.5", className)}>
            {items.map((item) => (
                <button
                    key={item.value}
                    type="button"
                    onClick={() => onChange(item.value)}
                    className={cn(
                        "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors",
                        value === item.value
                            ? "bg-primary text-primary-foreground shadow-sm"
                            : "bg-secondary text-secondary-foreground hover:bg-secondary/80"
                    )}
                >
                    {item.label}
                    {item.count !== undefined && (
                        <span
                            className={cn(
                                "inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold",
                                value === item.value
                                    ? "bg-primary-foreground/20 text-primary-foreground"
                                    : "bg-foreground/10 text-foreground/60"
                            )}
                        >
                            {item.count}
                        </span>
                    )}
                </button>
            ))}
        </div>
    );
}
