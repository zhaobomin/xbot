import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Bot, Puzzle, Radio, Server, SlidersHorizontal } from "lucide-react";
import { PageHeader } from "../components/business/page-header";
import { StatusDot } from "../components/business/status-dot";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import { useChannels } from "../hooks/use-channels";
import { useMCPRuntime } from "../hooks/use-mcp";
import { useProviders } from "../hooks/use-providers";

export default function Integrations() {
    const { t } = useTranslation();
    const { data: channels, isLoading: loadingChannels } = useChannels();
    const { data: providers, isLoading: loadingProviders } = useProviders();
    const { data: mcpRuntime, isLoading: loadingMcp } = useMCPRuntime();

    const runningChannels = channels?.filter((channel) => channel.running).length ?? 0;
    const channelErrors = channels?.filter((channel) => channel.error) ?? [];
    const configuredProviders = providers?.filter((provider) => provider.has_key).length ?? 0;
    const runningMcp = mcpRuntime?.filter((server) => server.running).length ?? 0;
    const totalMcpTools = mcpRuntime?.reduce((sum, server) => sum + (server.tool_count ?? 0), 0) ?? 0;

    const sections = [
        {
            title: t("nav.channels"),
            description: t("integrations.channelsDesc"),
            href: "/channels",
            icon: Radio,
            loading: loadingChannels,
            status: `${runningChannels} / ${channels?.length ?? 0}`,
            statusLabel: t("dashboard.running"),
        },
        {
            title: t("nav.tools"),
            description: t("integrations.toolsDesc"),
            href: "/tools",
            icon: Puzzle,
            loading: loadingMcp,
            status: `${totalMcpTools}`,
            statusLabel: t("integrations.availableTools"),
        },
        {
            title: t("nav.mcp"),
            description: t("integrations.mcpDesc"),
            href: "/tools?tab=mcp",
            icon: Server,
            loading: loadingMcp,
            status: `${runningMcp} / ${mcpRuntime?.length ?? 0}`,
            statusLabel: t("dashboard.running"),
        },
        {
            title: t("nav.providers"),
            description: t("integrations.providersDesc"),
            href: "/settings?tab=providers",
            icon: Bot,
            loading: loadingProviders,
            status: `${configuredProviders} / ${providers?.length ?? 0}`,
            statusLabel: t("providers.configured"),
        },
    ];

    return (
        <div className="space-y-6">
            <PageHeader
                title={t("nav.integrations")}
                description={t("integrations.description")}
                actions={
                    <Button asChild variant="outline">
                        <Link to="/settings?tab=providers">
                            <SlidersHorizontal className="mr-2 h-4 w-4" />
                            {t("integrations.configureProviders")}
                        </Link>
                    </Button>
                }
            />

            {channelErrors.length > 0 && (
                <Alert variant="destructive">
                    <AlertTitle>{t("integrations.channelWarnings")}</AlertTitle>
                    <AlertDescription>
                        {channelErrors.slice(0, 2).map((channel) => channel.name).join(", ")}
                        {channelErrors.length > 2 ? ` +${channelErrors.length - 2}` : ""}
                    </AlertDescription>
                </Alert>
            )}

            <div className="grid gap-4 md:grid-cols-2">
                {sections.map((section) => {
                    const Icon = section.icon;
                    return (
                        <Card key={section.href} className="transition-colors hover:bg-muted/20">
                            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                                <div className="space-y-1">
                                    <CardTitle>{section.title}</CardTitle>
                                    <CardDescription>{section.description}</CardDescription>
                                </div>
                                <div className="flex h-9 w-9 items-center justify-center rounded-lg border bg-background">
                                    <Icon className="h-4 w-4 text-muted-foreground" />
                                </div>
                            </CardHeader>
                            <CardContent className="flex items-center justify-between gap-4">
                                {section.loading ? (
                                    <Skeleton className="h-5 w-24" />
                                ) : (
                                    <StatusDot
                                        tone={section.status.startsWith("0") ? "muted" : "success"}
                                        label={`${section.status} ${section.statusLabel}`}
                                    />
                                )}
                                <Button asChild variant="ghost" size="sm">
                                    <Link to={section.href}>
                                        {t("common.manage")}
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Link>
                                </Button>
                            </CardContent>
                        </Card>
                    );
                })}
            </div>
        </div>
    );
}
