import { cn } from "../../lib/utils";
import { Label } from "../ui/label";

interface FormSectionProps {
    title: string;
    description?: string;
    children: React.ReactNode;
    className?: string;
}

export function FormSection({ title, description, children, className }: FormSectionProps) {
    return (
        <div className={cn("space-y-4", className)}>
            <div>
                <h3 className="text-sm font-medium text-foreground">{title}</h3>
                {description && <p className="text-xs text-muted-foreground">{description}</p>}
            </div>
            <div className="space-y-4">{children}</div>
        </div>
    );
}

interface FormRowProps {
    children: React.ReactNode;
    className?: string;
}

export function FormRow({ children, className }: FormRowProps) {
    return <div className={cn("grid gap-4 md:grid-cols-2", className)}>{children}</div>;
}

interface FormFieldProps {
    label: string;
    required?: boolean;
    children: React.ReactNode;
    error?: string;
}

export function FormField({ label, required, children, error }: FormFieldProps) {
    return (
        <div className="space-y-1.5">
            <Label className="text-sm font-medium text-foreground">
                {label}
                {required && <span className="ml-0.5 text-destructive">*</span>}
            </Label>
            {children}
            {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
    );
}

interface PageActionsProps {
    children: React.ReactNode;
    className?: string;
}

export function PageActions({ children, className }: PageActionsProps) {
    return (
        <div className={cn("flex items-center justify-end gap-3 pt-4 border-t border-border", className)}>
            {children}
        </div>
    );
}
