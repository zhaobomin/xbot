import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuthStore } from "../stores/auth-store";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
    Card,
    CardContent,
    CardHeader,
    CardTitle,
    CardDescription,
} from "../components/ui/card";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { User, Lock, Languages } from "lucide-react";

export default function Login() {
    const { t, i18n } = useTranslation();
    const navigate = useNavigate();
    const setAuth = useAuthStore((s) => s.setAuth);
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [loading, setLoading] = useState(false);

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!username.trim() || !password.trim()) {
            toast.error(t("auth.fieldRequired"));
            return;
        }
        setLoading(true);
        try {
            const res = await api.post("/auth/login", { username, password });
            const { access_token, user } = res.data;
            setAuth(user, access_token);
            navigate("/dashboard");
        } catch {
            toast.error(t("auth.loginFailed"));
        } finally {
            setLoading(false);
        }
    };

    const LANG_LABELS: Record<string, string> = { zh: "中文", en: "English" };
    const getLanguageLabel = () => LANG_LABELS[i18n.language] ?? "English";

    return (
        <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-gradient-to-br from-violet-50 via-white to-purple-100 dark:from-gray-950 dark:via-gray-900 dark:to-violet-950 px-4">
            {/* Decorative background circles */}
            <div className="absolute top-0 left-0 w-96 h-96 bg-violet-200/30 dark:bg-violet-900/20 rounded-full blur-3xl -translate-x-1/2 -translate-y-1/2" />
            <div className="absolute bottom-0 right-0 w-96 h-96 bg-purple-300/30 dark:bg-purple-800/20 rounded-full blur-3xl translate-x-1/2 translate-y-1/2" />

            {/* Language switcher */}
            <div className="absolute top-4 right-4">
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button
                            variant="outline"
                            size="sm"
                            className="gap-2 backdrop-blur-sm bg-white/50 dark:bg-gray-900/50"
                        >
                            <Languages className="h-4 w-4" />
                            {getLanguageLabel()}
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                        {Object.entries(LANG_LABELS).map(([code, label]) => (
                            <DropdownMenuItem
                                key={code}
                                onClick={() => i18n.changeLanguage(code)}
                            >
                                {label}
                            </DropdownMenuItem>
                        ))}
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>

            <Card className="w-full max-w-sm shadow-2xl backdrop-blur-sm bg-white/80 dark:bg-gray-900/80 border-violet-200/50 dark:border-violet-900/50 animate-in fade-in zoom-in duration-500">
                <CardHeader className="text-center space-y-3 pb-4">
                    <div className="flex justify-center">
                        <div className="relative">
                            <div className="absolute inset-0 rounded-3xl bg-violet-400/20 blur-xl animate-pulse" />
                            <div className="relative flex h-16 min-w-[96px] items-center justify-center rounded-3xl border border-violet-200/70 bg-white/90 px-5 shadow-lg dark:border-violet-900/50 dark:bg-gray-900/90">
                                <span className="text-3xl font-black lowercase tracking-tight bg-gradient-to-r from-violet-500 via-purple-500 to-indigo-600 bg-clip-text text-transparent">
                                    xbot
                                </span>
                            </div>
                        </div>
                    </div>
                    <div>
                        <CardTitle className="text-2xl font-bold bg-gradient-to-r from-violet-600 to-purple-500 bg-clip-text text-transparent">
                            xbot
                        </CardTitle>
                        <CardDescription className="text-sm mt-1">
                            {t("auth.login")}
                        </CardDescription>
                    </div>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleLogin} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="username" className="text-sm font-medium">
                                {t("auth.username")}
                            </Label>
                            <div className="relative">
                                <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-primary" />
                                <Input
                                    id="username"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    autoComplete="username"
                                    className="pl-10 h-10"
                                    placeholder={t("auth.username")}
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="password" className="text-sm font-medium">
                                {t("auth.password")}
                            </Label>
                            <div className="relative">
                                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-primary" />
                                <Input
                                    id="password"
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    autoComplete="current-password"
                                    className="pl-10 h-10"
                                    placeholder={t("auth.password")}
                                />
                            </div>
                        </div>
                        <Button
                            type="submit"
                            className="w-full h-10 mt-6 font-semibold shadow-xl shadow-primary/30 transition-all duration-300 hover:scale-[1.02]"
                            disabled={loading}
                        >
                            {loading ? t("common.loading") : t("auth.loginButton")}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}
