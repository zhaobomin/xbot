import axios from "axios";
import { useAuthStore } from "../stores/auth-store";
import { getGatewayApiBaseUrl, useGatewayStore } from "../stores/gateway-store";

export { useGatewayStore };

const api = axios.create({
    timeout: 30000,
});

api.interceptors.request.use((config) => {
    config.baseURL = getGatewayApiBaseUrl();
    const token = useAuthStore.getState().token;
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

api.interceptors.response.use(
    (response) => response,
    (error) => {
        return Promise.reject(error);
    }
);

export default api;

// Bind destination and credentials together; global gateway changes cannot reroute an in-flight operation.
export function gatewayApi(baseUrl: string) {
    const token = useAuthStore.getState().token;
    return axios.create({
        baseURL: `${baseUrl.replace(/\/$/, "")}/api`,
        timeout: 30000,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
}
