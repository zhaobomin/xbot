import {
    Clock,
    List,
    MessageSquare,
    Plug,
    type LucideIcon,
} from "lucide-react";

export interface AppNavItem {
    path: string;
    label: string;
    icon: LucideIcon;
    adminOnly?: boolean;
    aliases?: string[];
}

export const PRIMARY_NAV_ITEMS: AppNavItem[] = [
    { path: "/chat", label: "nav.chat", icon: MessageSquare },
    { path: "/sessions", label: "nav.sessionList", icon: List },
    { path: "/cron", label: "nav.automation", icon: Clock, adminOnly: true },
    {
        path: "/integrations",
        label: "nav.integrations",
        icon: Plug,
        adminOnly: true,
        aliases: ["/channels", "/tools", "/mcp", "/skills", "/providers"],
    },
];

export const MOBILE_NAV_ITEMS = PRIMARY_NAV_ITEMS.filter((item) =>
    ["/chat"].includes(item.path)
);

export function isNavItemActive(pathname: string, item: AppNavItem) {
    const candidates = [item.path, ...(item.aliases ?? [])];
    return candidates.some((path) => {
        if (path === "/dashboard") return pathname === path;
        return pathname === path || pathname.startsWith(`${path}/`);
    });
}
