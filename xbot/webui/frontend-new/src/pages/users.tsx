import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { SectionHeader } from "../components/business/section-header";
import api from "../lib/api";
import { useAuthStore } from "../stores/auth-store";
import { Badge } from "../components/ui/badge";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "../components/ui/table";
import { useQuery } from "@tanstack/react-query";

interface UserInfo {
    id: string;
    username: string;
    role: "admin" | "user";
}

export default function Users() {
    const { t } = useTranslation();
    const currentUser = useAuthStore((s) => s.user);

    const { data: users, isLoading } = useQuery<UserInfo[]>({
        queryKey: ["users"],
        queryFn: () => api.get("/users").then((r) => r.data),
    });
    const [copiedHintShown, setCopiedHintShown] = useState(false);

    return (
        <div className="space-y-4">
            <SectionHeader title={t("nav.users")} />

            <div className="rounded-lg border bg-muted/30 p-4">
                <div className="flex items-center gap-2">
                    <Badge>{t("users.singleAdminBadge")}</Badge>
                    <span className="text-sm font-medium">{t("users.singleAdminTitle")}</span>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{t("users.singleAdminDesc")}</p>
                <button
                    type="button"
                    className="mt-3 text-sm text-primary underline-offset-4 hover:underline"
                    onClick={() => {
                        navigator.clipboard.writeText("xbot webui serve");
                        setCopiedHintShown(true);
                        toast.success(t("users.singleAdminActionDone"));
                    }}
                >
                    {t("users.singleAdminAction")}
                </button>
                {copiedHintShown && (
                    <p className="mt-2 text-xs text-muted-foreground">{t("users.singleAdminActionDone")}</p>
                )}
            </div>

            <div className="rounded-md border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>{t("users.username")}</TableHead>
                            <TableHead>{t("users.role")}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {users?.map((u) => (
                            <TableRow key={u.id} className="hover:bg-muted/20">
                                <TableCell className="font-mono font-medium">
                                    {u.username}
                                    {u.id === currentUser?.id && (
                                        <span className="ml-2 text-xs text-muted-foreground">(you)</span>
                                    )}
                                </TableCell>
                                <TableCell>
                                    <Badge variant={u.role === "admin" ? "default" : "secondary"}>
                                        {u.role === "admin" ? t("users.admin") : t("users.user")}
                                    </Badge>
                                </TableCell>
                            </TableRow>
                        ))}
                        {(!users || users.length === 0) && !isLoading && (
                            <TableRow>
                                <TableCell colSpan={2} className="text-center text-muted-foreground">
                                    {t("common.noData")}
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </div>
        </div>
    );
}
