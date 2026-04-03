import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRegisterSW } from "virtual:pwa-register/react";
import { Button } from "../ui/button";
import { RefreshCw } from "lucide-react";

export function PWAUpdateToast() {
    const { t } = useTranslation();
    const [show, setShow] = useState(false);

    const {
        needRefresh: [needRefresh],
        updateServiceWorker,
    } = useRegisterSW({
        onRegisteredSW(_swUrl, r) {
            if (r) {
                setInterval(() => r.update(), 60 * 60 * 1000);
            }
        },
    });

    useEffect(() => {
        setShow(needRefresh);
    }, [needRefresh]);

    if (!show) return null;

    return (
        <div className="fixed bottom-4 right-4 z-50 flex items-center gap-3 rounded-xl border bg-card p-4 shadow-panel animate-in slide-in-from-bottom-4">
            <RefreshCw className="h-5 w-5 text-primary" />
            <div className="flex-1">
                <p className="text-sm font-medium">{t("common.updateAvailable")}</p>
            </div>
            <Button size="sm" onClick={() => updateServiceWorker(true)}>
                {t("common.refresh")}
            </Button>
        </div>
    );
}
