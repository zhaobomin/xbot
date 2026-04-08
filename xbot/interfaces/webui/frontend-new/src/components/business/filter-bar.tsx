import { Search } from "lucide-react";
import { Input } from "../ui/input";
import { cn } from "../../lib/utils";

interface FilterBarProps {
    search: string;
    onSearchChange: (v: string) => void;
    placeholder?: string;
    actions?: React.ReactNode;
    className?: string;
}

export function FilterBar({
    search,
    onSearchChange,
    placeholder,
    actions,
    className,
}: FilterBarProps) {
    return (
        <div className={cn("flex items-center gap-3", className)}>
            <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                    value={search}
                    onChange={(e) => onSearchChange(e.target.value)}
                    placeholder={placeholder}
                    className="pl-9 h-9"
                />
            </div>
            {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
    );
}
