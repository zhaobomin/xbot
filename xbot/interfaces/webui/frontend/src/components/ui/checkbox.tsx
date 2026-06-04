import * as React from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
    label?: string;
}

const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
    ({ className, label, id, ...props }, ref) => {
        const inputId = id ?? React.useId();
        return (
            <label htmlFor={inputId} className="inline-flex items-center gap-2 text-sm">
                <span className="relative inline-flex h-4 w-4 shrink-0 items-center justify-center">
                    <input
                        ref={ref}
                        id={inputId}
                        type="checkbox"
                        className={cn(
                            "peer h-4 w-4 appearance-none rounded border border-input bg-background transition-colors checked:border-primary checked:bg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
                            className
                        )}
                        {...props}
                    />
                    <Check className="pointer-events-none absolute h-3 w-3 text-primary-foreground opacity-0 peer-checked:opacity-100" />
                </span>
                {label && <span>{label}</span>}
            </label>
        );
    }
);
Checkbox.displayName = "Checkbox";

export { Checkbox };
