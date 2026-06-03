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
    const [error, setError] = useState("");

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
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
            setError(t("auth.loginFailed"));
        } finally {
            setLoading(false);
        }
    };

    const LANG_LABELS: Record<string, string> = { zh: "\u4e2d\u6587", en: "English" };
    const getLanguageLabel = () => LANG_LABELS[i18n.language] ?? "English";

    return (
        <div className="relative flex min-h-screen items-center justify-center bg-background px-4">
            {/* Language switcher */}
            <div className="absolute top-4 right-4">
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="outline" size="sm" className="gap-2">
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

            <Card className="w-full max-w-sm rounded-2xl border border-border/60 bg-card shadow-panel">
                <CardHeader className="text-center space-y-3 pb-6">
                    <div className="flex justify-center">
                        <div className="flex h-14 min-w-[84px] items-center justify-center rounded-2xl border border-border bg-secondary/50 px-5">
                            <span className="text-2xl font-black lowercase tracking-tight bg-gradient-to-r from-violet-500 via-purple-500 to-indigo-600 bg-clip-text text-transparent">
                                xbot
                            </span>
                        </div>
                    </div>
                    <div>
                        <CardTitle className="text-xl font-bold text-foreground">
                            xbot
                        </CardTitle>
                        <CardDescription className="text-sm mt-1">
                            {t("auth.login")}
                        </CardDescription>
                    </div>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleLogin} className="space-y-5">
                        {error && (
                            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                                {error}
                            </div>
                        )}
                        <div className="space-y-2">
                            <Label htmlFor="username" className="text-sm font-medium">
                                {t("auth.username")}
                            </Label>
                            <div className="relative">
                                <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                <Input
                                    id="username"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    autoComplete="username"
                                    className="pl-10 h-11"
                                    placeholder={t("auth.username")}
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="password" className="text-sm font-medium">
                                {t("auth.password")}
                            </Label>
                            <div className="relative">
                                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                <Input
                                    id="password"
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    autoComplete="current-password"
                                    className="pl-10 h-11"
                                    placeholder={t("auth.password")}
                                />
                            </div>
                        </div>
                        <Button
                            type="submit"
                            className="w-full h-11 mt-8 font-semibold"
                            isLoading={loading}
                        >
                            {loading ? t("common.loading") : t("auth.loginButton")}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}
