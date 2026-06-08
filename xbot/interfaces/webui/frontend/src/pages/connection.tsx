import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
    getDefaultGatewayUrl,
    normalizeGatewayUrl,
    useGatewayStore,
} from "../stores/gateway-store";

function isHttpUrl(value: string): boolean {
    try {
        const parsed = new URL(value);
        return parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch {
        return false;
    }
}

export default function Connection() {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const savedUrl = useGatewayStore((s) => s.gatewayUrl);
    const setGatewayUrl = useGatewayStore((s) => s.setGatewayUrl);
    const [url, setUrl] = useState(savedUrl || getDefaultGatewayUrl());
    const [testing, setTesting] = useState(false);
    const [status, setStatus] = useState<string>("");

    const testConnection = async (candidate: string) => {
        const normalized = normalizeGatewayUrl(candidate);
        if (!isHttpUrl(normalized)) {
            throw new Error("Use an http:// or https:// gateway URL.");
        }
        const response = await fetch(`${normalized}/api/desktop/ping`, {
            method: "GET",
            credentials: "include",
        });
        if (!response.ok) {
            throw new Error(`Gateway returned HTTP ${response.status}.`);
        }
        const payload = await response.json();
        if (!payload?.ok) {
            throw new Error("Gateway ping response was invalid.");
        }
        return payload;
    };

    const handleTest = async () => {
        setTesting(true);
        setStatus("");
        try {
            const payload = await testConnection(url);
            setStatus(`Connected to ${payload.name ?? "xbot"} ${payload.version ?? ""}`.trim());
        } catch (error) {
            const message = error instanceof Error ? error.message : "Connection failed.";
            setStatus(message);
        } finally {
            setTesting(false);
        }
    };

    const handleSave = async () => {
        setTesting(true);
        try {
            await testConnection(url);
            setGatewayUrl(normalizeGatewayUrl(url));
            queryClient.clear();
            toast.success("Gateway saved");
            navigate("/chat");
        } catch (error) {
            const message = error instanceof Error ? error.message : "Connection failed.";
            setStatus(message);
            toast.error(message);
        } finally {
            setTesting(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-background px-4">
            <div className="w-full max-w-md space-y-5">
                <div className="space-y-1">
                    <h1 className="text-xl font-semibold text-foreground">XBot Gateway</h1>
                    <p className="text-sm text-muted-foreground">
                        Connect this desktop client to an already-running gateway.
                    </p>
                </div>

                <div className="space-y-2">
                    <Label htmlFor="gateway-url">Gateway URL</Label>
                    <Input
                        id="gateway-url"
                        value={url}
                        onChange={(event) => setUrl(event.target.value)}
                        placeholder="http://127.0.0.1:18780"
                    />
                </div>

                {status && (
                    <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
                        {status}
                    </p>
                )}

                <div className="flex justify-end gap-2">
                    <Button variant="ghost" onClick={handleTest} disabled={testing}>
                        Test
                    </Button>
                    <Button onClick={handleSave} disabled={testing}>
                        Save
                    </Button>
                </div>
            </div>
        </div>
    );
}
