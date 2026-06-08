import { create } from "zustand";

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
    (set) => ({
        user: DEFAULT_USER,
        token: null,
        setAuth: (user, token) => set({ user, token }),
        clearAuth: () => set({ user: DEFAULT_USER, token: null }),
    })
);
