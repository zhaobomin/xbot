import { useEffect } from "react";

/**
 * iOS Safari keyboard / zoom fix for web-app-like behavior.
 *
 * 1. Tracks visual viewport height → --vvh so the layout shrinks above the keyboard.
 * 2. Temporarily locks maximum-scale=1 on input focus to prevent zoom.
 */
export function useIOSInputFix() {
    useEffect(() => {
        const vv = window.visualViewport;

        const updateVvh = () => {
            const h = vv ? vv.height : window.innerHeight;
            const kh = Math.max(0, window.innerHeight - h);
            document.documentElement.style.setProperty("--vvh", `${h}px`);
            document.documentElement.style.setProperty("--keyboard-height", `${kh}px`);
            if (window.scrollY !== 0 || window.scrollX !== 0) {
                window.scrollTo(0, 0);
            }
        };

        if (vv) {
            vv.addEventListener("resize", updateVvh);
            vv.addEventListener("scroll", updateVvh);
        }
        window.addEventListener("resize", updateVvh);
        updateVvh();

        const getViewportMeta = () =>
            document.querySelector<HTMLMetaElement>('meta[name="viewport"]');

        const isInput = (el: EventTarget | null) =>
            el instanceof HTMLElement && el.matches("input, textarea, select");

        const onFocus = (e: FocusEvent) => {
            if (!isInput(e.target)) return;
            const meta = getViewportMeta();
            if (!meta || meta.content.includes("maximum-scale")) return;
            meta.content += ", maximum-scale=1";
        };

        const onBlur = (e: FocusEvent) => {
            if (!isInput(e.target)) return;
            const meta = getViewportMeta();
            if (!meta) return;
            meta.content = meta.content.replace(/,?\s*maximum-scale=\S*/g, "").trim();
        };

        document.addEventListener("focus", onFocus, true);
        document.addEventListener("blur", onBlur, true);

        return () => {
            if (vv) {
                vv.removeEventListener("resize", updateVvh);
                vv.removeEventListener("scroll", updateVvh);
            }
            window.removeEventListener("resize", updateVvh);
            document.removeEventListener("focus", onFocus, true);
            document.removeEventListener("blur", onBlur, true);
        };
    }, []);
}
