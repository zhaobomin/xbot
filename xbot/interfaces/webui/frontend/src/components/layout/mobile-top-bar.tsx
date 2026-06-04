import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useTheme } from "next-themes";
import { useAuthStore } from "../../stores/auth-store";
import { cn } from "../../lib/utils";
import { Sun, Moon, Languages, LogOut, KeyRound, Sparkles } from "lucide-react";
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
import { ChangePasswordDialog } from "./change-password-dialog";

export function MobileTopBar() {
    const { t, i18n } = useTranslation();
    const { resolvedTheme, setTheme } = useTheme();
    const user = useAuthStore((s) => s.user);
    const clearAuth = useAuthStore((s) => s.clearAuth);
    const [showChangePwd, setShowChangePwd] = useState(false);

    const LANG_LABELS: Record<string, string> = { zh: "中文", en: "English" };
    const currentLangLabel = LANG_LABELS[i18n.language] ?? "English";
    const isDark = resolvedTheme === "dark";

    return (
        <>
            <header
                className="fixed top-0 left-0 right-0 z-40 flex h-12 items-center justify-between bg-background/85 px-4 backdrop-blur-xl"
                style={{
                    paddingTop: "env(safe-area-inset-top)",
                    boxShadow: "var(--shadow-down)",
                }}
            >
                {/* Logo - unified with sidebar */}
                <div className="flex items-center gap-1.5">
                    <div className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
                        <Sparkles className="h-3 w-3" />
                    </div>
                    <span className="text-base font-semibold tracking-tight text-foreground">
                        XBot
                    </span>
                </div>

                {/* Right actions */}
                <div className="flex items-center gap-1">
                    <button
                        onClick={() => setTheme(isDark ? "light" : "dark")}
                        title={isDark ? t("common.lightMode") : t("common.darkMode")}
                        className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                        {isDark ? (
                            <Sun className="h-4 w-4" />
                        ) : (
                            <Moon className="h-4 w-4" />
                        )}
                    </button>

                    <DropdownMenu>
                        <DropdownMenuTrigger
                            title={user?.username}
                            className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15 text-xs font-bold text-primary transition-colors hover:bg-primary/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                        >
                            {user?.username?.[0]?.toUpperCase() ?? "?"}
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-48">
                            <div className={cn("px-2 py-1.5 text-xs text-muted-foreground")}>
                                {user?.username}
                            </div>
                            <DropdownMenuSeparator />
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
                </div>
            </header>

            <ChangePasswordDialog
                open={showChangePwd}
                onClose={() => setShowChangePwd(false)}
            />
        </>
    );
}
