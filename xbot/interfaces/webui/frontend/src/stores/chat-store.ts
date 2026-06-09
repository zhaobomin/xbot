import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ChatMessage {
    id: string;
    role: "user" | "assistant" | "tool" | "system" | "sub_tool";
    content: string;
    timestamp: string;
    isStreaming?: boolean;
    toolCalls?: ToolCallInfo[];
    name?: string;
    isSubAgent?: boolean;
    serverIndex?: number;
}

export interface ToolCallInfo {
    id: string;
    name: string;
    input?: string;
    output?: string;
}

interface SessionState {
    isWaiting: boolean;
    progressText: string;
}

interface ChatState {
    currentSessionKey: string | null;
    messages: ChatMessage[];
    showToolMessages: boolean;
    mobileShowChat: boolean;
    sessionStates: Record<string, SessionState>;
    isWaiting: boolean;
    progressText: string;
    setMobileShowChat: (v: boolean) => void;
    setCurrentSession: (key: string | null) => void;
    addMessage: (msg: ChatMessage) => void;
    appendAssistantText: (id: string, text: string) => void;
    setStreaming: (id: string, isStreaming: boolean) => void;
    setProgress: (text: string, sessionKey?: string) => void;
    setWaiting: (v: boolean, sessionKey?: string) => void;
    clearMessages: () => void;
    setMessages: (msgs: ChatMessage[]) => void;
    toggleToolMessages: () => void;
    getSessionState: (key: string) => SessionState;
}

const DEFAULT_SESSION_STATE: SessionState = { isWaiting: false, progressText: "" };

export const useChatStore = create<ChatState>()(
    persist(
        (set, get) => ({
            currentSessionKey: null,
            messages: [],
            showToolMessages: true,
            mobileShowChat: false,
            sessionStates: {},

            get isWaiting() {
                const s = get();
                return (s.sessionStates[s.currentSessionKey ?? ""] ?? DEFAULT_SESSION_STATE).isWaiting;
            },
            get progressText() {
                const s = get();
                return (s.sessionStates[s.currentSessionKey ?? ""] ?? DEFAULT_SESSION_STATE).progressText;
            },

            setMobileShowChat: (v) => set({ mobileShowChat: v }),

            setCurrentSession: (key) =>
                set((state) => ({
                    currentSessionKey: key,
                    messages: state.currentSessionKey === key ? state.messages : [],
                })),

            addMessage: (msg) =>
                set((state) => ({ messages: [...state.messages, msg] })),

            appendAssistantText: (id, text) =>
                set((state) => ({
                    messages: state.messages.map((m) =>
                        m.id === id ? { ...m, content: m.content + text } : m
                    ),
                })),

            setStreaming: (id, isStreaming) =>
                set((state) => ({
                    messages: state.messages.map((m) =>
                        m.id === id ? { ...m, isStreaming } : m
                    ),
                })),

            setProgress: (progressText, sessionKey?) =>
                set((state) => {
                    const key = sessionKey ?? state.currentSessionKey ?? "";
                    const prev = state.sessionStates[key] ?? DEFAULT_SESSION_STATE;
                    return {
                        sessionStates: { ...state.sessionStates, [key]: { ...prev, progressText } },
                    };
                }),

            setWaiting: (isWaiting, sessionKey?) =>
                set((state) => {
                    const key = sessionKey ?? state.currentSessionKey ?? "";
                    const prev = state.sessionStates[key] ?? DEFAULT_SESSION_STATE;
                    return {
                        sessionStates: {
                            ...state.sessionStates,
                            [key]: { ...prev, isWaiting, ...(isWaiting ? {} : { progressText: "" }) },
                        },
                    };
                }),

            clearMessages: () => set({ messages: [] }),

            setMessages: (messages) => set({ messages }),

            toggleToolMessages: () =>
                set((state) => ({ showToolMessages: !state.showToolMessages })),

            getSessionState: (key) => {
                return get().sessionStates[key] ?? DEFAULT_SESSION_STATE;
            },
        }),
        {
            name: "xbot-chat",
            version: 1,
            migrate: (persistedState) => {
                const state = persistedState as Partial<Pick<ChatState, "currentSessionKey" | "messages" | "showToolMessages">>;
                return {
                    currentSessionKey: state.currentSessionKey ?? null,
                    messages: state.messages ?? [],
                    showToolMessages: true,
                };
            },
            partialize: (state) => ({
                currentSessionKey: state.currentSessionKey,
                messages: state.messages,
                showToolMessages: state.showToolMessages,
            }),
        }
    )
);
