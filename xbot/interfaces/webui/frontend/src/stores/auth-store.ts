import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface UserInfo {
    id: string;
    username: string;
    role: "admin" | "user";
}

interface AuthState {
    user: UserInfo;
    token: string | null;
    setAuth: (user: UserInfo, token: string) => void;
    clearAuth: () => void;
}

const DEFAULT_USER: UserInfo = { id: "admin", username: "admin", role: "admin" };

export const useAuthStore = create<AuthState>()(
    persist(
        (set) => ({
        user: DEFAULT_USER,
        token: null,
        setAuth: (user, token) => set({ user, token }),
        clearAuth: () => set({ user: DEFAULT_USER, token: null }),
        }),
        { name: "xbot-auth" }
    )
);
