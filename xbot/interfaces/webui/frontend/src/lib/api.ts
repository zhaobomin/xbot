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
