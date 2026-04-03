import type { Config } from "tailwindcss";

const config: Config = {
    darkMode: "class",
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                border: "hsl(var(--border))",
                input: "hsl(var(--input))",
                ring: "hsl(var(--ring))",
                background: "hsl(var(--background))",
                foreground: "hsl(var(--foreground))",
                primary: {
                    DEFAULT: "hsl(var(--primary))",
                    foreground: "hsl(var(--primary-foreground))",
                },
                secondary: {
                    DEFAULT: "hsl(var(--secondary))",
                    foreground: "hsl(var(--secondary-foreground))",
                },
                destructive: {
                    DEFAULT: "hsl(var(--destructive))",
                    foreground: "hsl(var(--destructive-foreground))",
                },
                muted: {
                    DEFAULT: "hsl(var(--muted))",
                    foreground: "hsl(var(--muted-foreground))",
                },
                accent: {
                    DEFAULT: "hsl(var(--accent))",
                    foreground: "hsl(var(--accent-foreground))",
                },
                popover: {
                    DEFAULT: "hsl(var(--popover))",
                    foreground: "hsl(var(--popover-foreground))",
                },
                card: {
                    DEFAULT: "hsl(var(--card))",
                    foreground: "hsl(var(--card-foreground))",
                },
                success: {
                    DEFAULT: "hsl(var(--success))",
                    foreground: "hsl(var(--success-foreground))",
                },
                warning: {
                    DEFAULT: "hsl(var(--warning))",
                    foreground: "hsl(var(--warning-foreground))",
                },
            },
            borderRadius: {
                xl: "1rem",
                "2xl": "1.4rem",
                lg: "var(--radius)",
                md: "calc(var(--radius) - 2px)",
                sm: "calc(var(--radius) - 4px)",
            },
            fontFamily: {
                sans: [
                    "Plus Jakarta Sans",
                    "-apple-system",
                    "BlinkMacSystemFont",
                    "Segoe UI",
                    "PingFang SC",
                    "Hiragino Sans GB",
                    "Microsoft YaHei",
                    "sans-serif",
                ],
                mono: ["JetBrains Mono", "Fira Code", "monospace"],
            },
            fontSize: {
                xs: ["12px", { lineHeight: "1.5" }],
                sm: ["13px", { lineHeight: "1.5" }],
                base: ["14px", { lineHeight: "1.6" }],
                lg: ["16px", { lineHeight: "1.5" }],
                xl: ["20px", { lineHeight: "1.4" }],
                "2xl": ["24px", { lineHeight: "1.3" }],
            },
            boxShadow: {
                subtle: "0 1px 2px 0 rgba(0, 0, 0, 0.03)",
                soft: "0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02)",
                panel: "0 4px 12px rgba(0, 0, 0, 0.06), 0 1px 3px rgba(0, 0, 0, 0.04)",
                "card-hover": "0 2px 8px rgba(0, 0, 0, 0.06)",
            },
            keyframes: {
                "fade-in-up": {
                    "0%": { opacity: "0", transform: "translateY(8px)" },
                    "100%": { opacity: "1", transform: "translateY(0)" },
                },
                blink: {
                    "0%, 100%": { opacity: "1" },
                    "50%": { opacity: "0" },
                },
                "slide-in-from-right": {
                    from: { transform: "translateX(100%)" },
                    to: { transform: "translateX(0)" },
                },
                "slide-in-from-bottom": {
                    from: { transform: "translateY(100%)" },
                    to: { transform: "translateY(0)" },
                },
                "accordion-down": {
                    from: { height: "0" },
                    to: { height: "var(--radix-accordion-content-height)" },
                },
                "accordion-up": {
                    from: { height: "var(--radix-accordion-content-height)" },
                    to: { height: "0" },
                },
                pulse: {
                    "0%, 100%": { opacity: "1" },
                    "50%": { opacity: "0.4" },
                },
            },
            animation: {
                "fade-in-up": "fade-in-up 0.15s ease-out both",
                blink: "blink 1s step-end infinite",
                "slide-in-right": "slide-in-from-right 200ms ease-out",
                "slide-in-bottom": "slide-in-from-bottom 200ms ease",
                "accordion-down": "accordion-down 200ms ease-out",
                "accordion-up": "accordion-up 200ms ease-out",
                pulse: "pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite",
            },
        },
    },
    plugins: [require("tailwindcss-animate"), require("@tailwindcss/typography")],
};

export default config;
