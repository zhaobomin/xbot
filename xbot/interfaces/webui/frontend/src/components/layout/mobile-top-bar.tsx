import { useTranslation } from "react-i18next";
import { useTheme } from "next-themes";
import { Sun, Moon } from "lucide-react";

export function MobileTopBar() {
    const { t } = useTranslation();
    const { resolvedTheme, setTheme } = useTheme();
    const isDark = resolvedTheme === "dark";

    return (
        <header
            className="fixed top-0 left-0 right-0 z-40 flex h-12 items-center justify-between bg-background/85 px-4 backdrop-blur-xl"
            style={{
                paddingTop: "env(safe-area-inset-top)",
                boxShadow: "var(--shadow-down)",
            }}
        >
            <div className="flex items-center rounded-xl px-1">
                <img
                    src="/xbot-logo.svg?v=xbot-logo-20260604b"
                    alt="XBot"
                    className="h-8 w-auto"
                />
            </div>

            <button
                onClick={() => setTheme(isDark ? "light" : "dark")}
                title={isDark ? t("common.lightMode") : t("common.darkMode")}
                className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
                {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
        </header>
    );
}
