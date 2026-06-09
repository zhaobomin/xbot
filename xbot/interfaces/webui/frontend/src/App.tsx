import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./components/layout/app-layout";
import { useAuthStore } from "./stores/auth-store";

const Login = lazy(() => import("./pages/login"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Chat = lazy(() => import("./pages/Chat"));
const Channels = lazy(() => import("./pages/Channels"));
const Integrations = lazy(() => import("./pages/integrations"));
const Tools = lazy(() => import("./pages/Tools"));
const CronJobs = lazy(() => import("./pages/cron-jobs"));
const Settings = lazy(() => import("./pages/Settings"));
const SystemConfig = lazy(() => import("./pages/system-config"));
const Connection = lazy(() => import("./pages/connection"));

function PrivateRoute({ children }: { children: ReactNode }) {
    const token = useAuthStore((s) => s.token);
    return token ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
    return (
        <Suspense fallback={null}>
            <Routes>
                <Route path="/login" element={<Login />} />
                <Route path="/connection" element={<Connection />} />
                <Route
                    path="/"
                    element={
                        <PrivateRoute>
                            <AppLayout />
                        </PrivateRoute>
                    }
                >
                    <Route index element={<Navigate to="/chat" replace />} />
                    <Route path="dashboard" element={<Dashboard />} />
                    <Route path="sessions" element={<Chat />} />
                    <Route path="chat" element={<Chat />} />
                    <Route path="chat/:sessionKey" element={<Chat />} />
                    <Route
                        path="providers"
                        element={<Navigate to="/settings?tab=providers" replace />}
                    />
                    <Route path="integrations" element={<Integrations />} />
                    <Route path="channels" element={<Channels />} />
                    <Route
                        path="mcp"
                        element={<Navigate to="/tools?tab=mcp" replace />}
                    />
                    <Route
                        path="skills"
                        element={<Navigate to="/tools?tab=skills" replace />}
                    />
                    <Route path="tools" element={<Tools />} />
                    <Route path="cron" element={<CronJobs />} />
                    <Route path="settings" element={<Settings />} />
                    <Route path="users" element={<Navigate to="/settings" replace />} />
                    <Route path="system-config" element={<SystemConfig />} />
                </Route>
                <Route path="*" element={<Navigate to="/chat" replace />} />
            </Routes>
        </Suspense>
    );
}
