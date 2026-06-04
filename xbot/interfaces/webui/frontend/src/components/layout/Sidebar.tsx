import { useLocation, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTheme } from "next-themes";
import { useAuthStore } from "../../stores/auth-store";
import { cn } from "../../lib/utils";
import {
    isNavItemActive,
    PRIMARY_NAV_ITEMS,
    type AppNavItem,
} from "../../lib/navigation";
import {
    Sun,
    Moon,
    Languages,
    LogOut,
    KeyRound,
    PanelLeftClose,
    PanelLeftOpen,
    Bot,
    Settings,
} from "lucide-react";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuSub,
    DropdownMenuSubContent,
    DropdownMenuSubTrigger,
    DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "../ui/tooltip";
import { useState } from "react";
import { ChangePasswordDialog } from "./change-password-dialog";
import { SessionList } from "../business/session-list";

function NavLink({
    item,
    active,
    collapsed,
}: {
    item: AppNavItem;
    active: boolean;
    collapsed: boolean;
}) {
    const { t } = useTranslation();
    const Icon = item.icon;

    const linkContent = (
        <Link
            to={item.path}
            className={cn(
                "group relative flex items-center text-sm font-medium transition-colors duration-150",
                collapsed
                    ? "justify-center py-2.5 mx-auto w-10 rounded-lg"
                    : "gap-3 px-3 py-2 rounded-lg mx-1",
                active
                    ? collapsed
                        ? "bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))]"
                        : "bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))] font-semibold"
                    : cn(
                        "text-[hsl(var(--sidebar-fg))]",
                        "hover:bg-[hsl(var(--sidebar-hover-bg))]"
                    )
            )}
        >
            {active && !collapsed && (
                <span className="absolute left-0 top-1/2 h-[60%] w-[3px] -translate-y-1/2 rounded-full bg-primary" />
            )}
            <Icon
                className={cn(
                    "h-4 w-4 shrink-0 transition-colors duration-150",
                    active
                        ? "text-primary"
                        : "text-[hsl(var(--sidebar-muted))] group-hover:text-[hsl(var(--sidebar-fg))]"
                )}
            />
            {!collapsed && <span className="truncate">{t(item.label)}</span>}
        </Link>
    );

    if (collapsed) {
        return (
            <Tooltip delayDuration={0}>
                <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
                <TooltipContent side="right" className="font-medium">
                    {t(item.label)}
                </TooltipContent>
            </Tooltip>
        );
    }

    return linkContent;
}

interface SidebarProps {
    collapsed: boolean;
    onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
    const { t, i18n } = useTranslation();
    const { resolvedTheme, setTheme } = useTheme();
    const location = useLocation();
    const user = useAuthStore((s) => s.user);
    const clearAuth = useAuthStore((s) => s.clearAuth);
    const isAdmin = user?.role === "admin";
    const [showChangePwd, setShowChangePwd] = useState(false);

    const navItems = PRIMARY_NAV_ITEMS.filter((item) => (!item.adminOnly || isAdmin) && item.path !== "/sessions");
    const isActive = (item: AppNavItem) => isNavItemActive(location.pathname, item);
    const settingsActive =
        location.pathname === "/settings" ||
        location.pathname.startsWith("/settings/") ||
        location.pathname === "/users" ||
        location.pathname === "/system-config";

    const LANG_LABELS: Record<string, string> = { zh: "中文", en: "English" };
    const currentLangLabel = LANG_LABELS[i18n.language] ?? "English";

    return (
        <TooltipProvider>
            <aside
                className={cn(
                    "flex h-full flex-col border-r border-border/60 transition-[width] duration-300 ease-in-out overflow-hidden",
                    collapsed ? "w-[60px]" : "w-[240px]"
                )}
                style={{
                    background: "hsl(var(--sidebar-bg))",
                    boxShadow: "var(--sidebar-edge-shadow)",
                }}
            >
                <div
                    className={cn(
                        "group flex h-14 shrink-0 items-center",
                        collapsed ? "justify-center px-1" : "justify-between pl-3 pr-2"
                    )}
                >
                    {!collapsed && (
                        <div className="flex h-9 items-center gap-2">
                            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                                <Bot className="h-3.5 w-3.5" />
                            </div>
                            <span className="text-lg font-semibold tracking-tight text-foreground">
                                XBot
                            </span>
                        </div>
                    )}
                    {collapsed && (
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                            <Bot className="h-4 w-4" />
                        </div>
                    )}
                    {!collapsed && (
                        <button
                            onClick={onToggle}
                            title={t("nav.collapse")}
                            className={cn(
                                "flex h-7 w-7 items-center justify-center rounded-md transition-all duration-200",
                                "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]",
                                "opacity-0 group-hover:opacity-100"
                            )}
                        >
                            <PanelLeftClose className="h-4 w-4" />
                        </button>
                    )}
                </div>

                {collapsed && (
                    <div className="flex justify-center pb-1">
                        <button
                            onClick={onToggle}
                            title={t("nav.expand")}
                            className={cn(
                                "flex h-7 w-7 items-center justify-center rounded-md transition-all duration-200",
                                "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                            )}
                        >
                            <PanelLeftOpen className="h-4 w-4" />
                        </button>
                    </div>
                )}

                <nav className="flex-1 flex flex-col min-h-0 px-2 py-3">
                    {/* 工作区 */}
                    <div className="mb-2">
                        {!collapsed && (
                            <p
                                className="mb-2 px-4 text-xs font-semibold uppercase tracking-[0.15em]"
                                style={{ color: "hsl(var(--sidebar-section-label))" }}
                            >
                                {t("nav.section.workspace")}
                            </p>
                        )}
                        <div className="space-y-1">
                            {navItems.map((item) => (
                                <NavLink
                                    key={item.path}
                                    item={item}
                                    active={isActive(item)}
                                    collapsed={collapsed}
                                />
                            ))}
                        </div>
                    </div>

                    {/* 会话列表 */}
                    <div className="flex-1 flex flex-col min-h-0">
                        {!collapsed && (
                            <p
                                className="mb-2 px-4 text-xs font-semibold uppercase tracking-[0.15em]"
                                style={{ color: "hsl(var(--sidebar-section-label))" }}
                            >
                                {t("nav.sessionList")}
                            </p>
                        )}
                        {!collapsed && <SessionList />}
                    </div>
                </nav>

                <div className="shrink-0 px-2 pb-3">
                    <div className="mx-2 mb-3 h-px bg-border" />

                    {collapsed ? (
                        <div className="flex flex-col items-center gap-1.5">
                            {isAdmin && (
                                <Tooltip delayDuration={0}>
                                    <TooltipTrigger asChild>
                                        <Link
                                            to="/settings"
                                            className={cn(
                                                "flex h-8 w-8 items-center justify-center rounded-lg transition-colors duration-150",
                                                settingsActive
                                                    ? "bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))]"
                                                    : "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                                            )}
                                        >
                                            <Settings className="h-3.5 w-3.5" />
                                        </Link>
                                    </TooltipTrigger>
                                    <TooltipContent side="right">
                                        {t("nav.settings")}
                                    </TooltipContent>
                                </Tooltip>
                            )}
                            <DropdownMenu>
                                <DropdownMenuTrigger
                                    title={user?.username}
                                    className="flex h-8 w-8 items-center justify-center rounded-full border bg-background transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                                >
                                    <span className="text-xs font-semibold text-foreground">
                                        {user?.username?.[0]?.toUpperCase() ?? "?"}
                                    </span>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent side="right" align="end" className="w-48">
                                    <DropdownMenuSub>
                                        <DropdownMenuSubTrigger>
                                            <Languages className="mr-2 h-4 w-4" />
                                            {currentLangLabel}
                                        </DropdownMenuSubTrigger>
                                        <DropdownMenuSubContent>
                                            {Object.entries(LANG_LABELS).map(([code, label]) => (
                                                <DropdownMenuItem
                                                    key={code}
                                                    onClick={() => i18n.changeLanguage(code)}
                                                    className={
                                                        i18n.language === code
                                                            ? "font-semibold text-primary"
                                                            : ""
                                                    }
                                                >
                                                    {label}
                                                </DropdownMenuItem>
                                            ))}
                                        </DropdownMenuSubContent>
                                    </DropdownMenuSub>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem onClick={() => setShowChangePwd(true)}>
                                        <KeyRound className="mr-2 h-4 w-4" />
                                        {t("auth.changePassword")}
                                    </DropdownMenuItem>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem
                                        onClick={clearAuth}
                                        className="text-destructive focus:text-destructive"
                                    >
                                        <LogOut className="mr-2 h-4 w-4" />
                                        {t("auth.logout")}
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                            <Tooltip delayDuration={0}>
                                <TooltipTrigger
                                    onClick={() =>
                                        setTheme(resolvedTheme === "dark" ? "light" : "dark")
                                    }
                                    className={cn(
                                        "flex h-8 w-8 items-center justify-center rounded-lg transition-colors duration-150",
                                        "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                                    )}
                                >
                                    {resolvedTheme === "dark" ? (
                                        <Sun className="h-3.5 w-3.5" />
                                    ) : (
                                        <Moon className="h-3.5 w-3.5" />
                                    )}
                                </TooltipTrigger>
                                <TooltipContent side="right">
                                    {resolvedTheme === "dark"
                                        ? t("common.lightMode")
                                        : t("common.darkMode")}
                                </TooltipContent>
                            </Tooltip>
                        </div>
                    ) : (
                        <div className="space-y-0.5">
                            {/* User row */}
                            <DropdownMenu>
                                <DropdownMenuTrigger
                                    className={cn(
                                        "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150",
                                        "text-[hsl(var(--sidebar-fg))] hover:bg-[hsl(var(--sidebar-hover-bg))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                                    )}
                                >
                                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-background">
                                        <span className="text-xs font-semibold text-foreground">
                                            {user?.username?.[0]?.toUpperCase() ?? "?"}
                                        </span>
                                    </div>
                                    <span className="flex-1 truncate text-left">
                                        {user?.username}
                                    </span>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent side="right" align="end" className="w-48">
                                    <DropdownMenuSub>
                                        <DropdownMenuSubTrigger>
                                            <Languages className="mr-2 h-4 w-4" />
                                            {currentLangLabel}
                                        </DropdownMenuSubTrigger>
                                        <DropdownMenuSubContent>
                                            {Object.entries(LANG_LABELS).map(([code, label]) => (
                                                <DropdownMenuItem
                                                    key={code}
                                                    onClick={() => i18n.changeLanguage(code)}
                                                    className={
                                                        i18n.language === code
                                                            ? "font-semibold text-primary"
                                                            : ""
                                                    }
                                                >
                                                    {label}
                                                </DropdownMenuItem>
                                            ))}
                                        </DropdownMenuSubContent>
                                    </DropdownMenuSub>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem onClick={() => setShowChangePwd(true)}>
                                        <KeyRound className="mr-2 h-4 w-4" />
                                        {t("auth.changePassword")}
                                    </DropdownMenuItem>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem
                                        onClick={clearAuth}
                                        className="text-destructive focus:text-destructive"
                                    >
                                        <LogOut className="mr-2 h-4 w-4" />
                                        {t("auth.logout")}
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>

                            <div className="grid grid-cols-2 gap-1">
                                <button
                                    onClick={() =>
                                        setTheme(resolvedTheme === "dark" ? "light" : "dark")
                                    }
                                    className={cn(
                                        "flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors duration-150",
                                        "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                                    )}
                                >
                                    {resolvedTheme === "dark" ? (
                                        <Sun className="h-4 w-4" />
                                    ) : (
                                        <Moon className="h-4 w-4" />
                                    )}
                                    <span className="truncate">
                                        {resolvedTheme === "dark"
                                            ? t("common.lightMode")
                                            : t("common.darkMode")}
                                    </span>
                                </button>
                                {isAdmin && (
                                    <Link
                                        to="/settings"
                                        className={cn(
                                            "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150",
                                            settingsActive
                                                ? "bg-[hsl(var(--sidebar-active-bg))] text-[hsl(var(--sidebar-active-fg))]"
                                                : "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                                        )}
                                    >
                                        <Settings className="h-4 w-4" />
                                        <span>{t("nav.settings")}</span>
                                    </Link>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                <ChangePasswordDialog
                    open={showChangePwd}
                    onClose={() => setShowChangePwd(false)}
                />
            </aside>
        </TooltipProvider>
    );
}
