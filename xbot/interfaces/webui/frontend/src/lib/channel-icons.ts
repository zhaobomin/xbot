import {
    Building2,
    Compass,
    Mail,
    MessageCircle,
    MessageSquare,
    Monitor,
    Send,
    Slack,
    Globe,
    type LucideIcon,
} from "lucide-react";

/**
 * Emoji-based icon map for quick reference (used in chat page).
 * Kept for backward compatibility.
 */
export const CHANNEL_ICONS: Record<string, string> = {
    weixin: "💚",
    wecom: "🏢",
    im: "💬",
    web: "💬",
    telegram: "✈️",
    whatsapp: "💬",
    discord: "🎮",
    feishu: "🐦",
    dingtalk: "🔔",
    email: "📧",
    slack: "⚡",
    qq: "🐧",
    matrix: "🔷",
    mochat: "💼",
};

/**
 * Lucide icon map for channel types — cleaner, consistent with the UI.
 * Each entry maps a channel name to a Lucide icon component.
 */
const CHANNEL_LUCIDE: Record<string, LucideIcon> = {
    weixin: MessageCircle,    // WeChat green → message circle
    wecom: Building2,         // Enterprise WeChat → building
    im: MessageCircle,        // Unified external IM namespace
    web: Monitor,             // Web UI → monitor
    telegram: Send,           // Telegram paper airplane → send
    whatsapp: MessageSquare,  // WhatsApp → message square
    discord: Compass,         // Discord → compass (gaming/communication)
    feishu: Send,             // Feishu → send (dove/flight → send)
    dingtalk: MessageSquare,  // DingTalk → message square
    email: Mail,              // Email → mail
    slack: Slack,             // Slack → slack logo
    qq: MessageCircle,        // QQ → message circle
    matrix: Globe,            // Matrix → globe (decentralized network)
    mochat: MessageCircle,    // MoChat → message circle
};

/**
 * Get the Lucide icon component for a channel key.
 * Falls back to MessageCircle for unknown channels.
 */
export function getChannelIcon(key: string): LucideIcon {
    const channel = key.split(":")[0] ?? "web";
    return CHANNEL_LUCIDE[channel] ?? Monitor;
}
