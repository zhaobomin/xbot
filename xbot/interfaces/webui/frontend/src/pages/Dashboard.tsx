import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Activity, AlertCircle, ArrowRight, Clock, MessageSquare, Radio, Server } from "lucide-react";
import { PageHeader } from "../components/business/page-header";
import { StatusDot } from "../components/business/status-dot";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent } from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import { useChannels } from "../hooks/use-channels";
import { useCronJobs } from "../hooks/use-cron";
import { useSessions } from "../hooks/use-sessions";
import { cn } from "../lib/utils";

export default function Dashboard() {
    const { t } = useTranslation();
    const { data: channels, isLoading: loadingChannels } = useChannels();
    const { data: cron, isLoading: loadingCron } = useCronJobs();
    const { data: sessions, isLoading: loadingSessions } = useSessions();

    const runningChannels = channels?.filter((channel) => channel.running).length ?? 0;
    const totalChannels = channels?.length ?? 0;
    const channelErrors = channels?.filter((channel) => channel.error) ?? [];
    const enabledCron = cron?.filter((job) => job.enabled).length ?? 0;
    const totalCron = cron?.length ?? 0;
    const totalSessions = sessions?.length ?? 0;
    const hasDataError = channelErrors.length > 0;

    const overview = [
        {
            label: t("dashboard.gatewayHealth"),
            value: hasDataError ? t("dashboard.degraded") : t("dashboard.online"),
            detail: t("dashboard.gatewayDetail"),
            icon: Server,
            loading: loadingChannels || loadingCron || loadingSessions,
            tone: hasDataError ? "warning" : "success",
        },
        {
            label: t("dashboard.sessions"),
            value: `${totalSessions}`,
            detail: t("dashboard.activeSessions"),
            icon: MessageSquare,
            loading: loadingSessions,
            tone: totalSessions > 0 ? "success" : "muted",
        },
        {
            label: t("dashboard.runningChannels"),
            value: `${runningChannels} / ${totalChannels}`,
            detail: t("dashboard.running"),
            icon: Radio,
            loading: loadingChannels,
            tone: runningChannels > 0 ? "success" : "muted",
        },
        {
            label: t("dashboard.scheduledTasks"),
            value: `${enabledCron} / ${totalCron}`,
            detail: t("dashboard.active"),
            icon: Clock,
            loading: loadingCron,
            tone: enabledCron > 0 ? "success" : "muted",
        },
    ] as const;

    return (
        <div className="space-y-6">
            <PageHeader
                title={t("nav.dashboard")}
                description={t("dashboard.description")}
                actions={
                    <Button asChild>
                        <Link to="/chat">
                            <MessageSquare className="mr-2 h-4 w-4" />
                            {t("dashboard.openChat")}
                        </Link>
                    </Button>
                }
            />

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                {overview.map((item) => {
                    const Icon = item.icon;
                    return (
                        <Card key={item.label}>
                            <CardContent className="p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0 space-y-2">
                                        <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                                            {item.label}
                                        </p>
                                        {item.loading ? (
                                            <Skeleton className="h-8 w-20" />
                                        ) : (
                                            <div className="text-2xl font-semibold tracking-tight">{item.value}</div>
                                        )}
                                        <StatusDot tone={item.tone} label={item.detail} />
                                    </div>
                                    <div className="flex h-9 w-9 items-center justify-center rounded-lg border bg-background">
                                        <Icon className="h-4 w-4 text-muted-foreground" />
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    );
                })}
            </div>

            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
                <Card>
                    <CardContent className="space-y-3 p-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <h2 className="text-base font-semibold">{t("dashboard.recentIssues")}</h2>
                                <p className="text-sm text-muted-foreground">{t("dashboard.recentIssuesDesc")}</p>
                            </div>
                            <Activity className="h-4 w-4 text-muted-foreground" />
                        </div>
                        {channelErrors.length === 0 ? (
                            <Alert variant="success">
                                <AlertTitle>{t("dashboard.noWarnings")}</AlertTitle>
                                <AlertDescription>{t("dashboard.noWarningsDesc")}</AlertDescription>
                            </Alert>
                        ) : (
                            <div className="space-y-2">
                                {channelErrors.slice(0, 4).map((channel) => (
                                    <Alert key={channel.name} variant="destructive">
                                        <div className="flex gap-2">
                                            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                                            <div>
                                                <AlertTitle>{channel.name}</AlertTitle>
                                                <AlertDescription className="line-clamp-2">{channel.error}</AlertDescription>
                                            </div>
                                        </div>
                                    </Alert>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardContent className="space-y-2 p-4">
                        <h2 className="text-base font-semibold">{t("dashboard.quickActions")}</h2>
                        {[
                            ["/integrations", t("nav.integrations")],
                            ["/cron", t("nav.automation")],
                            ["/settings", t("nav.settings")],
                        ].map(([href, label]) => (
                            <Button
                                key={href}
                                asChild
                                variant="ghost"
                                className={cn("w-full justify-between")}
                            >
                                <Link to={href}>
                                    {label}
                                    <ArrowRight className="h-4 w-4" />
                                </Link>
                            </Button>
                        ))}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
