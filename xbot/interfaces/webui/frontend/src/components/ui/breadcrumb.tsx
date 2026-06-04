import * as React from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

function Breadcrumb({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
    return <nav aria-label="breadcrumb" className={cn("text-sm text-muted-foreground", className)} {...props} />;
}

function BreadcrumbList({ className, ...props }: React.HTMLAttributes<HTMLOListElement>) {
    return <ol className={cn("flex flex-wrap items-center gap-1.5", className)} {...props} />;
}

function BreadcrumbItem({ className, ...props }: React.HTMLAttributes<HTMLLIElement>) {
    return <li className={cn("inline-flex items-center gap-1.5", className)} {...props} />;
}

function BreadcrumbSeparator() {
    return <ChevronRight className="h-3.5 w-3.5" />;
}

export { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbSeparator };
