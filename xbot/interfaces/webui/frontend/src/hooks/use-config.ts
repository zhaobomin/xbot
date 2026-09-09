import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import api, { gatewayApi } from "../lib/api";
import i18n from "../i18n";
import { useGatewayBaseUrl } from "../stores/gateway-store";

export interface AgentSettings {
    model: string;
    provider: string;
    max_iterations: number;
    context_window_tokens: number;
    workspace: string;
    send_progress?: boolean;
    send_tool_hints?: boolean;
}

export interface GatewayConfig {
    host: string;
    port: number;
    heartbeat_enabled: boolean;
    heartbeat_interval_s: number;
}

export function useAgentSettings() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    return useQuery<AgentSettings>({
        queryKey: ["config", gatewayBaseUrl, "agent"],
        queryFn: () => api.get("/config/agent").then((r) => r.data),
    });
}

export function useUpdateAgentSettings() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    const qc = useQueryClient();
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: (data: Partial<AgentSettings>) =>
            api.patch("/config/agent", data).then((r) => r.data),
        onSuccess: (_data, _vars, context) => {
            qc.invalidateQueries({ queryKey: ["config", context?.gatewayBaseUrl ?? gatewayBaseUrl, "agent"] });
        },
    });
}

export function useGatewayConfig() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    return useQuery<GatewayConfig>({
        queryKey: ["config", gatewayBaseUrl, "gateway"],
        queryFn: () => api.get("/config/gateway").then((r) => r.data),
    });
}

export function useUpdateGatewayConfig() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    const qc = useQueryClient();
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: (data: Partial<GatewayConfig>) =>
            api.patch("/config/gateway", data).then((r) => r.data),
        onSuccess: (_data, _vars, context) => {
            qc.invalidateQueries({ queryKey: ["config", context?.gatewayBaseUrl ?? gatewayBaseUrl, "gateway"] });
            toast.success(i18n.t("settings.saved"));
        },
    });
}

export function useWorkspaceFile(name: string) {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    return useQuery<{ name: string; content: string }>({
        queryKey: ["config", gatewayBaseUrl, "workspace-file", name],
        queryFn: () => api.get(`/config/workspace-file/${name}`).then((r) => r.data),
        enabled: !!name,
    });
}

export function useSaveWorkspaceFile() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    const qc = useQueryClient();
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: ({ name, content }: { name: string; content: string }) =>
            api.put(`/config/workspace-file/${name}`, { content }).then((r) => r.data),
        onSuccess: (data, vars, context) => {
            const key = ["config", context?.gatewayBaseUrl ?? gatewayBaseUrl, "workspace-file", vars.name];
            qc.setQueryData(key, data);
            qc.invalidateQueries({ queryKey: key });
            toast.success(i18n.t("settings.saved"));
        },
    });
}

export async function exportWorkspace(): Promise<void> {
    const resp = await api.get("/config/workspace/export", {
        responseType: "blob",
    });
    const cd: string = resp.headers["content-disposition"] ?? "";
    const match = cd.match(/filename=([^\s;]+)/);
    const filename = match ? match[1] : "workspace.zip";
    const url = URL.createObjectURL(resp.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

export function useImportWorkspace() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    const qc = useQueryClient();
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: (file: File) => {
            const form = new FormData();
            form.append("file", file);
            return api
                .post<{ ok: boolean; backup: string | null }>(
                    "/config/workspace/import",
                    form,
                    { headers: { "Content-Type": "multipart/form-data" } }
                )
                .then((r) => r.data);
        },
        onSuccess: (data, _vars, context) => {
            qc.invalidateQueries({ queryKey: ["config", context?.gatewayBaseUrl ?? gatewayBaseUrl, "workspace-file"] });
            const msg = data.backup
                ? i18n.t("sysconfig.importSuccessBackup", { path: data.backup })
                : i18n.t("sysconfig.importSuccess");
            toast.success(msg);
        },
        onError: () => toast.error(i18n.t("sysconfig.importFailed")),
    });
}

export function useRawConfig() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    return useQuery<{ content: string }>({
        queryKey: ["config", gatewayBaseUrl, "raw"],
        queryFn: () => api.get("/config/raw").then((r) => r.data),
    });
}

export function useSaveRawConfig() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    const qc = useQueryClient();
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: (content: string) =>
            api.put("/config/raw", { content }).then((r) => r.data),
        onSuccess: (_data, _vars, context) => {
            qc.invalidateQueries({ queryKey: ["config", context?.gatewayBaseUrl ?? gatewayBaseUrl] });
            toast.success(i18n.t("sysconfig.saved"));
        },
        onError: (err: unknown) => {
            const msg =
                (err as { response?: { data?: { detail?: string } } })?.response?.data
                    ?.detail ?? i18n.t("sysconfig.saveFailed");
            toast.error(msg);
        },
    });
}

// ---------------------------------------------------------------------------
// S3 / OSS Storage
// ---------------------------------------------------------------------------

export interface S3Config {
    enabled: boolean;
    endpoint_url: string;
    access_key_id: string;
    secret_access_key: string;
    bucket: string;
    region: string;
    public_base_url: string;
}

export function useS3Config() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    return useQuery<S3Config>({
        queryKey: ["config", gatewayBaseUrl, "s3"],
        queryFn: () => api.get("/config/s3").then((r) => r.data),
    });
}

export function useSaveS3Config() {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    const qc = useQueryClient();
    return useMutation({
        onMutate: () => ({ gatewayBaseUrl }),
        mutationFn: (data: Partial<S3Config>) =>
            api.put("/config/s3", data).then((r) => r.data),
        onSuccess: (_data, _vars, context) => {
            qc.invalidateQueries({ queryKey: ["config", context?.gatewayBaseUrl ?? gatewayBaseUrl, "s3"] });
            toast.success(i18n.t("s3.saved"));
        },
        onError: (err: unknown) => {
            const msg =
                (err as { response?: { data?: { detail?: string } } })?.response?.data
                    ?.detail ?? i18n.t("s3.saveFailed");
            toast.error(msg);
        },
    });
}

export async function uploadFile(file: File, client = api): Promise<{ id: string; url: string }> {
    const form = new FormData();
    form.append("file", file);
    const res = await client.post<{ id: string; url: string }>("/config/s3/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return res.data;
}

export function useLogs(lines: number = 500, keyword: string = "") {
    const gatewayBaseUrl = useGatewayBaseUrl();
    const api = gatewayApi(gatewayBaseUrl);
    return useQuery<{ content: string; path?: string }>({
        queryKey: ["config", gatewayBaseUrl, "logs", lines, keyword],
        queryFn: () =>
            api
                .get(
                    `/config/logs?lines=${lines}&keyword=${encodeURIComponent(keyword)}`
                )
                .then((r) => r.data),
        refetchInterval: 2000,
    });
}
