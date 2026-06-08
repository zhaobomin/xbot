import axios from "axios";
import { useAuthStore } from "../stores/auth-store";

const api = axios.create({
    baseURL: "/api",
    timeout: 30000,
});

api.interceptors.request.use((config) => {
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
