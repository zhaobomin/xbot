import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import api from "../lib/api";
import i18n from "../i18n";

export interface ProviderInfo {
    name: string;
    api_key_masked: string;
    api_base: string | null;
    extra_headers: Record<string, string> | null;
    has_key: boolean;
    models: string[];
    is_custom: boolean;
}

const PROVIDER_LABELS: Record<string, string> = {
    anthropic: "Anthropic",
    aliyun_coding_plan: "阿里云 Coding Plan",
    alrun: "Alrun",
};

const PROVIDER_DEFAULT_BASE_URLS: Record<string, string> = {
    anthropic: "https://api.anthropic.com",
    aliyun_coding_plan: "https://coding.dashscope.aliyuncs.com/apps/anthropic",
    alrun: "",
};

export function getProviderLabel(name: string): string {
    return PROVIDER_LABELS[name] ?? name;
}

export function getProviderDefaultBaseUrl(name: string): string {
    return PROVIDER_DEFAULT_BASE_URLS[name] ?? "";
}

export function useProviders() {
    return useQuery<ProviderInfo[]>({
        queryKey: ["providers"],
        queryFn: () => api.get("/providers").then((r) => r.data),
    });
}

export function useUpdateProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: ({
            name,
            api_key,
            api_base,
            extra_headers,
            models,
        }: {
            name: string;
            api_key?: string;
            api_base?: string;
            extra_headers?: Record<string, string>;
            models?: string[];
        }) =>
            api
                .patch(`/providers/${name}`, { api_key, api_base, extra_headers, models })
                .then((r) => r.data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["providers"] });
            toast.success(i18n.t("providers.saved"));
        },
    });
}

export function useCreateProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (data: {
            name: string;
            api_key?: string;
            api_base?: string;
            extra_headers?: Record<string, string>;
            models?: string[];
        }) => api.post("/providers", data).then((r) => r.data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["providers"] });
            toast.success(i18n.t("providers.created"));
        },
        onError: (err: unknown) => {
            const message =
                (err as { response?: { data?: { detail?: string } } })?.response?.data
                    ?.detail ?? "Failed to create provider";
            toast.error(message);
        },
    });
}

export function useDeleteProvider() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (name: string) =>
            api.delete(`/providers/${name}`).then((r) => r.data),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["providers"] });
            toast.success(i18n.t("providers.deleted"));
        },
    });
}
