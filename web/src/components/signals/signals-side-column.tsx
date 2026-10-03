"use client";

import { useState } from "react";
import { BellRing } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import {
  sendDiscordSignalTest,
  sendSignalTest,
} from "@/hooks/use-signals";
import type {
  DiscordRoute,
  SignalEntry,
  SignalsConfigResponse,
  SignalsStatus,
} from "@/lib/schemas";

import { DISCORD_TEST_ROUTES } from "./constants";
import { RecentSignalsTable } from "./recent-signals-table";

export function SignalsSideColumn({
  status,
  cfgData,
  signals,
}: {
  status?: SignalsStatus;
  cfgData?: SignalsConfigResponse;
  signals: SignalEntry[];
}) {
  const [testing, setTesting] = useState(false);
  const [discordTesting, setDiscordTesting] = useState<DiscordRoute | null>(null);

  const onTest = async () => {
    setTesting(true);
    try {
      await sendSignalTest();
      toast.success("Test message sent - check Telegram");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };

  const onDiscordTest = async (route: DiscordRoute) => {
    setDiscordTesting(route);
    try {
      await sendDiscordSignalTest(route);
      toast.success(`Test sent - check Discord (${route})`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed");
    } finally {
      setDiscordTesting(null);
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Signal bot</CardTitle>
          <CardDescription>
            Listens for completed candles and pushes pattern alerts (Telegram + optional Discord per watchlist)
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Badge variant={status?.running ? "default" : "secondary"}>
            {status?.running ? (
              <span className="size-1.5 rounded-full bg-primary-foreground" />
            ) : null}
            {status?.running ? "Listening" : "Not running"}
          </Badge>
          {status?.last_check && (
            <span className="text-xs text-muted-foreground">
              last check{" "}
              {new Date(status.last_check).toLocaleString()}
              {typeof status.heartbeat_age_s === "number"
                ? ` (${Math.round(status.heartbeat_age_s)}s ago)`
                : ""}
            </span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Telegram</CardTitle>
          <CardDescription>All watchlists share the same bot token and chat id</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          {cfgData && (
            <Badge variant={cfgData.telegram_configured ? "secondary" : "destructive"}>
              {cfgData.telegram_configured ? "Configured" : "Missing TELEGRAM_* in .env"}
            </Badge>
          )}
          <div className="ml-auto">
            <Button type="button" variant="outline" size="sm" onClick={onTest} disabled={testing}>
              {testing ? (
                <Spinner data-icon="inline-start" />
              ) : (
                <BellRing data-icon="inline-start" />
              )}
              {testing ? "Sending…" : "Send test"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Discord webhooks</CardTitle>
          <CardDescription>
            Each Discord timeframe in a watchlist uses its matching webhook automatically.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {cfgData?.discord_available_timeframes.length ? (
            <p className="text-xs text-muted-foreground">
              Available for signals:{" "}
              <span className="font-mono">{cfgData.discord_available_timeframes.join(", ")}</span>
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {DISCORD_TEST_ROUTES.map(({ route, label }) => {
              const ok = cfgData?.discord_configured?.[route];
              return (
                <Badge key={route} variant={ok ? "secondary" : "destructive"}>
                  {label} {ok ? "configured" : "missing webhook"}
                </Badge>
              );
            })}
          </div>
          <div className="flex flex-wrap gap-2">
            {DISCORD_TEST_ROUTES.map(({ route, label }) => {
              const configured = cfgData?.discord_configured?.[route];
              const busy = discordTesting === route;
              return (
                <Button
                  key={route}
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!configured || busy || discordTesting !== null}
                  onClick={() => onDiscordTest(route)}
                >
                  {busy ? <Spinner data-icon="inline-start" /> : null}
                  {busy ? "Sending…" : `Test ${label.toLowerCase()}`}
                </Button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <RecentSignalsTable signals={signals} />
    </div>
  );
}
