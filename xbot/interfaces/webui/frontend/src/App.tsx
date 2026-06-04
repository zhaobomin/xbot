import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuthStore } from "./stores/auth-store";
import AppLayout from "./components/layout/app-layout";

const Login = lazy(() => import("./pages/login"));
const Dashboard = lazy(() => import("./pages/dashboard"));
const Chat = lazy(() => import("./pages/chat"));
const Channels = lazy(() => import("./pages/channels"));
const Integrations = lazy(() => import("./pages/integrations"));
const Tools = lazy(() => import("./pages/tools"));
const CronJobs = lazy(() => import("./pages/cron-jobs"));
const Settings = lazy(() => import("./pages/settings"));
const Users = lazy(() => import("./pages/users"));
const SystemConfig = lazy(() => import("./pages/system-config"));

function PrivateRoute({ children }: { children: React.ReactNode }) {
    const token = useAuthStore((s) => s.token);
    const location = useLocation();
    const next = `${location.pathname}${location.search}`;
    return token ? <>{children}</> : <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
}

function AdminRoute({ children }: { children: React.ReactNode }) {
    const user = useAuthStore((s) => s.user);
    const location = useLocation();
    const next = `${location.pathname}${location.search}`;
    if (!user) return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
    if (user.role !== "admin") return <Navigate to="/dashboard" replace />;
    return <>{children}</>;
}

export default function App() {
    return (
        <Suspense fallback={null}>
            <Routes>
                <Route path="/login" element={<Login />} />
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
                    <Route path="sessions" element={<Navigate to="/chat" replace />} />
                    <Route path="chat" element={<Chat />} />
                    <Route path="chat/:sessionKey" element={<Chat />} />
                    <Route
                        path="providers"
                        element={<Navigate to="/settings?tab=providers" replace />}
                    />
                    <Route
                        path="integrations"
                        element={
                            <AdminRoute>
                                <Integrations />
                            </AdminRoute>
                        }
                    />
                    <Route
                        path="channels"
                        element={
                            <AdminRoute>
                                <Channels />
                            </AdminRoute>
                        }
                    />
                    <Route
                        path="mcp"
                        element={<Navigate to="/tools?tab=mcp" replace />}
                    />
                    <Route
                        path="tools"
                        element={
                            <AdminRoute>
                                <Tools />
                            </AdminRoute>
                        }
                    />
                    <Route
                        path="cron"
                        element={
                            <AdminRoute>
                                <CronJobs />
                            </AdminRoute>
                        }
                    />
                    <Route
                        path="settings"
                        element={
                            <AdminRoute>
                                <Settings />
                            </AdminRoute>
                        }
                    />
                    <Route
                        path="users"
                        element={
                            <AdminRoute>
                                <Users />
                            </AdminRoute>
                        }
                    />
                    <Route
                        path="system-config"
                        element={
                            <AdminRoute>
                                <SystemConfig />
                            </AdminRoute>
                        }
                    />
                </Route>
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
        </Suspense>
    );
}
