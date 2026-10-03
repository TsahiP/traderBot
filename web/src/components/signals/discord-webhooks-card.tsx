"use client";

import { useState } from "react";
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
  syncDiscordWebhooks,
  useDiscordSyncStatus,
  useSignalsConfig,
} from "@/hooks/use-signals";
import type { DiscordRoute, SignalsConfigResponse } from "@/lib/schemas";

import { DISCORD_TEST_ROUTE_SECTIONS } from "./constants";

export function DiscordWebhooksCard({
  cfgData,
}: {
  cfgData?: SignalsConfigResponse;
}) {
  const { mutate: refreshConfig } = useSignalsConfig();
  const { data: syncStatus, mutate: refreshSync } = useDiscordSyncStatus();
  const [discordTesting, setDiscordTesting] = useState<DiscordRoute | null>(null);
  const [syncing, setSyncing] = useState(false);

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

  const onSync = async () => {
    setSyncing(true);
    try {
      const result = await syncDiscordWebhooks(false);
      await Promise.all([refreshConfig(), refreshSync()]);
      const n = result.routes.length;
      toast.success(`Synced ${n} webhook route${n === 1 ? "" : "s"} from Discord`);
      if (result.conflicts.length) {
        toast.warning(
          `${result.conflicts.length} route conflict(s) — see card details`,
        );
      }
      if (result.skipped_no_webhook?.length) {
        toast.warning(
          `${result.skipped_no_webhook.length} channel(s) had no webhook (create in Discord or enable create_webhooks)`,
        );
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const configured = cfgData?.discord_configured;
  const configuredCount = configured
    ? Object.values(configured).filter(Boolean).length
    : 0;
  const syncedAt = syncStatus?.synced_at;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Discord webhooks</CardTitle>
        <CardDescription>
          Pattern alerts route by symbol (-USD = crypto) and timeframe. Env URLs override
          synced channels (names like stock-1h, crypto-5m).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={syncing || discordTesting !== null}
            onClick={onSync}
          >
            {syncing ? <Spinner data-icon="inline-start" /> : null}
            {syncing ? "Syncing…" : "Sync from Discord"}
          </Button>
          {syncedAt ? (
            <span className="text-xs text-muted-foreground">
              Last synced: <span className="font-mono">{syncedAt}</span>
            </span>
          ) : configuredCount > 0 ? (
            <span className="text-xs text-muted-foreground">
              {configuredCount} route(s) from .env — Sync optional (needs DISCORD_BOT_TOKEN +
              DISCORD_GUILD_ID)
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">
              Set webhooks in .env or Sync from Discord (DISCORD_BOT_TOKEN + DISCORD_GUILD_ID)
            </span>
          )}
        </div>
        {syncStatus?.conflicts?.length ? (
          <p className="text-xs text-destructive">
            Conflicts:{" "}
            {syncStatus.conflicts
              .map((c) => `${c.route} (${c.channels.join(" vs ")})`)
              .join("; ")}
          </p>
        ) : null}
        {cfgData?.discord_available_timeframes.length ? (
          <p className="text-xs text-muted-foreground">
            Available for signals:{" "}
            <span className="font-mono">
              {cfgData.discord_available_timeframes.join(", ")}
            </span>
          </p>
        ) : null}
        {DISCORD_TEST_ROUTE_SECTIONS.map((section) => (
          <div key={section.title} className="flex flex-col gap-2">
            <p className="text-xs font-medium text-muted-foreground">{section.title}</p>
            <div className="flex flex-wrap gap-2">
              {section.routes.map(({ route, label }) => {
                const ok = configured?.[route];
                return (
                  <Badge key={route} variant={ok ? "secondary" : "destructive"}>
                    {label} {ok ? "ok" : "missing"}
                  </Badge>
                );
              })}
            </div>
            <div className="flex flex-wrap gap-2">
              {section.routes.map(({ route, label }) => {
                const ok = configured?.[route];
                const busy = discordTesting === route;
                return (
                  <Button
                    key={`btn-${route}`}
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!ok || busy || discordTesting !== null}
                    onClick={() => onDiscordTest(route)}
                  >
                    {busy ? <Spinner data-icon="inline-start" /> : null}
                    {busy ? "Sending…" : `Test ${label}`}
                  </Button>
                );
              })}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
