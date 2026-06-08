import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./components/layout/app-layout";

const Dashboard = lazy(() => import("./pages/dashboard"));
const Chat = lazy(() => import("./pages/chat"));
const Channels = lazy(() => import("./pages/channels"));
const Integrations = lazy(() => import("./pages/integrations"));
const Tools = lazy(() => import("./pages/tools"));
const CronJobs = lazy(() => import("./pages/cron-jobs"));
const Settings = lazy(() => import("./pages/settings"));
const SystemConfig = lazy(() => import("./pages/system-config"));
const Connection = lazy(() => import("./pages/connection"));

export default function App() {
    return (
        <Suspense fallback={null}>
            <Routes>
                <Route path="/login" element={<Navigate to="/chat" replace />} />
                <Route path="/connection" element={<Connection />} />
                <Route
                    path="/"
                    element={<AppLayout />}
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
