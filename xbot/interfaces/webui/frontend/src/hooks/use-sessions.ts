import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import api, { gatewayApi } from "../lib/api";
import i18n from "../i18n";
import { useGatewayBaseUrl } from "../stores/gateway-store";

export interface SessionInfo {
    key: string;
    channel?: string;
    created_at: string;
    updated_at: string;
    first_message?: string;
    last_message?: string;
}

export interface MessageInfo {
    revision?: string;
    role: string;
    content: string | null;
    timestamp?: string;
    tool_calls?: unknown[];
    tool_call_id?: string;
    name?: string;
}

export function useSessions() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    return useQuery<SessionInfo[]>({
        queryKey: ["sessions", gatewayBaseUrl],
        queryFn: () => api.get("/sessions").then((r) => r.data),
    });
}

export function useSessionMessages(key: string) {
    const gatewayBaseUrl = useGatewayBaseUrl();
    return useQuery<MessageInfo[]>({
        queryKey: ["sessions", gatewayBaseUrl, key, "messages"],
        queryFn: () =>
            gatewayApi(gatewayBaseUrl).get(`/sessions/${encodeURIComponent(key)}/messages`).then((r) =>
                r.data.map((message: MessageInfo) => ({ ...message, revision: r.headers.etag }))),
        enabled: !!key,
    });
}

export function useSessionMemory(key: string) {
    const gatewayBaseUrl = useGatewayBaseUrl();
    return useQuery<MessageInfo[]>({
        queryKey: ["sessions", gatewayBaseUrl, key, "memory"],
        queryFn: () =>
            api.get(`/sessions/${encodeURIComponent(key)}/memory`).then((r) => r.data),
        enabled: !!key,
    });
}

export function useDeleteSession() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (key: string) =>
            api.delete(`/sessions/${encodeURIComponent(key)}`).then((r) => r.data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["sessions"] });
            toast.success(i18n.t("chat.sessionDeleted"));
        },
    });
}

export function useRevokeMessage() {
    const qc = useQueryClient();
    const gatewayBaseUrl = useGatewayBaseUrl();
    const boundApi = gatewayApi(gatewayBaseUrl);
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: ({ key, index, revision }: { key: string; index: number; revision: string }) =>
            boundApi.delete(`/sessions/${encodeURIComponent(key)}/messages/${index}`, {
                headers: { "If-Match": revision },
            }).then((r) => r.data),
        onSettled: async (_data, _error, vars, context) => {
            await qc.invalidateQueries({ queryKey: ["sessions", context?.gatewayBaseUrl ?? gatewayBaseUrl, vars.key, "messages"] });
        },
        onSuccess: () => {
            toast.success(i18n.t("chat.messageRevoked", "Message revoked"));
        },
        onError: () => toast.error(i18n.t("chat.historyChanged", "History changed or deletion failed. Refresh and try again.")),
    });
}
