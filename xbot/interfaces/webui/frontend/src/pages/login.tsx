import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Lock, User } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import api from "../lib/api";
import { useAuthStore } from "../stores/auth-store";

export default function Login() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const setAuth = useAuthStore((s) => s.setAuth);
    const [username, setUsername] = useState("admin");
    const [password, setPassword] = useState("");
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (!username.trim() || !password.trim()) {
            toast.error(t("auth.fieldRequired"));
            return;
        }

        setLoading(true);
        try {
            const response = await api.post("/auth/login", {
                username: username.trim(),
                password,
            });
            setAuth(response.data.user, response.data.access_token);
            navigate("/chat", { replace: true });
        } catch {
            toast.error(t("auth.loginFailed"));
        } finally {
            setLoading(false);
        }
    };

    return (
        <main className="flex min-h-screen items-center justify-center bg-background px-4">
            <Card className="w-full max-w-[360px]">
                <CardHeader className="space-y-2 text-center">
                    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg border border-border bg-secondary">
                        <img src="/icon.svg" alt="XBot" className="h-7 w-7" />
                    </div>
                    <CardTitle className="text-lg">XBot</CardTitle>
                    <CardDescription>{t("auth.login")}</CardDescription>
                </CardHeader>
                <CardContent>
                    <form className="space-y-4" onSubmit={handleSubmit}>
                        <div className="space-y-2">
                            <Label htmlFor="username">{t("auth.username")}</Label>
                            <div className="relative">
                                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                <Input
                                    id="username"
                                    autoComplete="username"
                                    className="pl-9"
                                    value={username}
                                    onChange={(event) => setUsername(event.target.value)}
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="password">{t("auth.password")}</Label>
                            <div className="relative">
                                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                <Input
                                    id="password"
                                    type="password"
                                    autoComplete="current-password"
                                    className="pl-9"
                                    value={password}
                                    onChange={(event) => setPassword(event.target.value)}
                                />
                            </div>
                        </div>
                        <Button type="submit" className="w-full" isLoading={loading}>
                            {t("auth.loginButton")}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </main>
    );
}
