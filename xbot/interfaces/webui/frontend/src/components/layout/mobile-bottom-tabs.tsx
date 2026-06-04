import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { cn } from "../../lib/utils";
import { isNavItemActive, MOBILE_NAV_ITEMS } from "../../lib/navigation";

export function MobileBottomTabs() {
    const { t } = useTranslation();
    const location = useLocation();

    return (
        <nav
            className="fixed bottom-0 left-0 right-0 z-40 flex border-t bg-background/95 backdrop-blur"
            style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
        >
            {MOBILE_NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                const active = isNavItemActive(location.pathname, item);
                return (
                    <Link
                        key={item.path}
                        to={item.path}
                        className={cn(
                            "flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium transition-colors",
                            active ? "text-foreground" : "text-muted-foreground"
                        )}
                    >
                        <Icon className="h-5 w-5" />
                        <span>{t(item.label)}</span>
                    </Link>
                );
            })}
        </nav>
    );
}
