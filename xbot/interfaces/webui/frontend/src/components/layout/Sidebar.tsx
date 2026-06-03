import { useLocation, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTheme } from "next-themes";
import { useAuthStore } from "../../stores/auth-store";
import { cn } from "../../lib/utils";
import {
    LayoutDashboard,
    MessageSquare,
    Radio,
    Puzzle,
    Clock,
    Settings,
    Users,
    FileJson,
    Sun,
    Moon,
    Languages,
    LogOut,
    KeyRound,
    PanelLeftClose,
    PanelLeftOpen,
    Sparkles,
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

interface NavItem {
    path: string;
    label: string;
    icon: React.ElementType;
}

const GENERAL_ITEMS: NavItem[] = [
    { path: "/dashboard", label: "nav.dashboard", icon: LayoutDashboard },
    { path: "/chat", label: "nav.chat", icon: MessageSquare },
];

const ADMIN_ITEMS: NavItem[] = [
    { path: "/settings", label: "nav.settings", icon: Settings },
    { path: "/channels", label: "nav.channels", icon: Radio },
    { path: "/tools", label: "nav.tools", icon: Puzzle },
    { path: "/users", label: "nav.users", icon: Users },
    { path: "/cron", label: "nav.cron", icon: Clock },
    { path: "/system-config", label: "nav.systemConfig", icon: FileJson },
];

function NavLink({
    item,
    active,
    collapsed,
}: {
    item: NavItem;
    active: boolean;
    collapsed: boolean;
}) {
    const { t } = useTranslation();
    const Icon = item.icon;

    const linkContent = (
        <Link
            to={item.path}
            className={cn(
                "group relative flex items-center text-sm font-medium transition-all duration-200",
                collapsed
                    ? "justify-center py-2.5 mx-auto w-10 rounded-lg"
                    : "gap-3 px-3 py-2 rounded-lg mx-1",
                active
                    ? collapsed
                        ? "bg-primary/12 text-primary"
                        : "bg-primary/10 text-primary font-semibold"
                    : cn(
                        "text-[hsl(var(--sidebar-fg))]",
                        "hover:bg-[hsl(var(--sidebar-hover-bg))] hover:translate-x-0.5"
                    )
            )}
        >
            {/* Active indicator bar - expanded only */}
            {active && !collapsed && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 h-[60%] w-[3px] rounded-full bg-primary" />
            )}
            <Icon
                className={cn(
                    "h-4 w-4 shrink-0 transition-colors duration-200",
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

    const isActive = (item: NavItem) =>
        location.pathname === item.path ||
        (item.path !== "/dashboard" && location.pathname.startsWith(item.path));

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
                {/* Logo + collapse toggle */}
                <div
                    className={cn(
                        "group flex h-14 shrink-0 items-center",
                        collapsed ? "justify-center px-1" : "justify-between pl-3 pr-2"
                    )}
                >
                    {!collapsed && (
                        <div className="flex h-9 items-center gap-2">
                            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                                <Sparkles className="h-3.5 w-3.5" />
                            </div>
                            <span className="text-lg font-semibold tracking-tight text-foreground">
                                XBot
                            </span>
                        </div>
                    )}
                    {collapsed && (
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
                            <Sparkles className="h-4 w-4" />
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

                {/* Expand button for collapsed state */}
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

                {/* Nav */}
                <nav className="flex-1 overflow-y-auto px-2 py-3">
                    {/* General section */}
                    <div className="mb-2">
                        {!collapsed && (
                            <p
                                className="mb-2 px-4 text-xs font-semibold uppercase tracking-[0.15em]"
                                style={{ color: "hsl(var(--sidebar-section-label))" }}
                            >
                                {t("nav.section.general")}
                            </p>
                        )}
                        <div className="space-y-1">
                            {GENERAL_ITEMS.map((item) => (
                                <NavLink
                                    key={item.path}
                                    item={item}
                                    active={isActive(item)}
                                    collapsed={collapsed}
                                />
                            ))}
                        </div>
                    </div>

                    {/* Admin section */}
                    {isAdmin && (
                        <div className={cn("mt-6 pt-4", collapsed && "mt-4 pt-2")}>
                            {/* Gradient separator */}
                            <div
                                className={cn("mx-3 mb-3 h-px", collapsed && "mx-2 mb-2")}
                                style={{
                                    background:
                                        "linear-gradient(to right, transparent, hsl(var(--sidebar-border)), transparent)",
                                }}
                            />
                            {!collapsed && (
                                <p
                                    className="mb-2 px-4 text-xs font-semibold uppercase tracking-[0.15em]"
                                    style={{ color: "hsl(var(--sidebar-section-label))" }}
                                >
                                    {t("nav.section.admin")}
                                </p>
                            )}
                            <div className="space-y-1">
                                {ADMIN_ITEMS.map((item) => (
                                    <NavLink
                                        key={item.path}
                                        item={item}
                                        active={isActive(item)}
                                        collapsed={collapsed}
                                    />
                                ))}
                            </div>
                        </div>
                    )}
                </nav>

                {/* Bottom: user + theme toggle */}
                <div className="shrink-0 px-2 pb-3">
                    {/* Gradient top border */}
                    <div
                        className="mx-2 mb-3 h-px"
                        style={{
                            background:
                                "linear-gradient(to right, transparent, hsl(var(--sidebar-border)), transparent)",
                        }}
                    />

                    {collapsed ? (
                        <div className="flex flex-col items-center gap-1.5">
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <button
                                        title={user?.username}
                                        className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15 ring-2 ring-primary/10 shadow-sm hover:ring-primary/25 transition-all duration-200"
                                    >
                                        <span className="text-xs font-bold text-primary">
                                            {user?.username?.[0]?.toUpperCase() ?? "?"}
                                        </span>
                                    </button>
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
                                <TooltipTrigger asChild>
                                    <button
                                        onClick={() =>
                                            setTheme(resolvedTheme === "dark" ? "light" : "dark")
                                        }
                                        className={cn(
                                            "flex h-8 w-8 items-center justify-center rounded-lg transition-all duration-200",
                                            "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                                        )}
                                    >
                                        {resolvedTheme === "dark" ? (
                                            <Sun className="h-3.5 w-3.5" />
                                        ) : (
                                            <Moon className="h-3.5 w-3.5" />
                                        )}
                                    </button>
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
                                <DropdownMenuTrigger asChild>
                                    <button
                                        className={cn(
                                            "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200",
                                            "text-[hsl(var(--sidebar-fg))] hover:bg-[hsl(var(--sidebar-hover-bg))]"
                                        )}
                                    >
                                        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 ring-2 ring-primary/10 shadow-sm">
                                            <span className="text-xs font-bold text-primary">
                                                {user?.username?.[0]?.toUpperCase() ?? "?"}
                                            </span>
                                        </div>
                                        <span className="flex-1 truncate text-left">
                                            {user?.username}
                                        </span>
                                    </button>
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

                            {/* Theme toggle row */}
                            <button
                                onClick={() =>
                                    setTheme(resolvedTheme === "dark" ? "light" : "dark")
                                }
                                className={cn(
                                    "flex w-full items-center gap-3 rounded-lg px-3 py-1.5 text-sm transition-all duration-200",
                                    "text-[hsl(var(--sidebar-muted))] hover:bg-[hsl(var(--sidebar-hover-bg))] hover:text-[hsl(var(--sidebar-fg))]"
                                )}
                            >
                                {resolvedTheme === "dark" ? (
                                    <Sun className="h-4 w-4" />
                                ) : (
                                    <Moon className="h-4 w-4" />
                                )}
                                <span>
                                    {resolvedTheme === "dark"
                                        ? t("common.lightMode")
                                        : t("common.darkMode")}
                                </span>
                            </button>
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
