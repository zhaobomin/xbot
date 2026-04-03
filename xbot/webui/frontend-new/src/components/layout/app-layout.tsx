import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./sidebar";
import { MobileBottomTabs } from "./mobile-bottom-tabs";
import { MobileTopBar } from "./mobile-top-bar";
import { useIsMobile } from "../../hooks/use-is-mobile";
import { useChatStore } from "../../stores/chat-store";
import { useIOSInputFix } from "../../hooks/use-ios-input-fix";
import { cn } from "../../lib/utils";

export default function AppLayout() {
    const { pathname } = useLocation();
    const isChatPage = pathname.startsWith("/chat");
    const [collapsed, setCollapsed] = useState(false);
    const isMobile = useIsMobile();
    const mobileShowChat = useChatStore((s) => s.mobileShowChat);
    useIOSInputFix();

    return (
        <div
            className="flex overflow-hidden bg-background"
            style={{
                position: "fixed",
                top: 0,
                left: 0,
                right: 0,
                height: "var(--vvh, 100dvh)",
            }}
        >
            {/* Sidebar: desktop only */}
            {!isMobile && (
                <Sidebar
                    collapsed={collapsed}
                    onToggle={() => setCollapsed((v) => !v)}
                />
            )}

            {/* Top bar: mobile only, hidden when inside chat window */}
            {isMobile && !mobileShowChat && <MobileTopBar />}

            <main
                className={cn(
                    "relative flex-1 min-w-0",
                    isChatPage
                        ? "flex flex-col overflow-hidden"
                        : cn("overflow-auto", isMobile ? "p-3 pt-14" : "p-4 lg:p-6")
                )}
                style={
                    isMobile
                        ? {
                            paddingBottom:
                                "max(env(safe-area-inset-bottom, 0px), calc(3.5rem - var(--keyboard-height, 0px)))",
                        }
                        : undefined
                }
            >
                <Outlet />
            </main>

            {/* Bottom tabs: mobile only */}
            {isMobile && <MobileBottomTabs />}
        </div>
    );
}
