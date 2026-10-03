"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useSignalsConfig,
  useSignalsHistory,
  useSignalsStatus,
} from "@/hooks/use-signals";

import { SignalsSideColumn } from "./signals-side-column";
import { WatchlistFormCard } from "./watchlist-form-card";

export function SignalsPanel() {
  const { data: cfgData, mutate: mutateConfig } = useSignalsConfig();
  const { data: status } = useSignalsStatus();
  const { data: history } = useSignalsHistory(50);

  const signals = history?.signals ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
      {cfgData ? (
        <WatchlistFormCard
          key={JSON.stringify(cfgData.config)}
          config={cfgData.config}
          patterns={cfgData.patterns}
          discordAvailable={cfgData.discord_available_timeframes}
          onSaved={() => mutateConfig()}
        />
      ) : (
        <Card className="h-fit">
          <CardContent className="flex flex-col gap-3 py-6">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      )}

      <SignalsSideColumn status={status} cfgData={cfgData} signals={signals} />
    </div>
  );
}
