type TauriWindow = Window & {
    __TAURI_INTERNALS__?: unknown;
};

export function isDesktopApp(): boolean {
    return (
        window.location.protocol === "tauri:" ||
        "__TAURI_INTERNALS__" in (window as TauriWindow)
    );
}

export function getClientSessionNamespace(): "app" | "web" {
    return isDesktopApp() ? "app" : "web";
}

export function getClientSessionPrefix(userId?: string | null): string {
    return `${getClientSessionNamespace()}:${userId ?? "admin"}:`;
}

export function createClientSessionKey(userId?: string | null, id = "default"): string {
    return `${getClientSessionPrefix(userId)}${id}`;
}

export function isWritableClientSession(sessionKey?: string | null): boolean {
    if (!sessionKey) return true;
    return sessionKey.startsWith(`${getClientSessionNamespace()}:`);
}
