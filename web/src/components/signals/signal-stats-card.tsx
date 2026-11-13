"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useSignalsStats } from "@/hooks/use-signals";
import type { OutcomeBucket } from "@/lib/schemas";

function winRate(bucket: OutcomeBucket) {
  const resolved = bucket.wins + bucket.losses;
  if (!resolved) return "—";
  return `${Math.round((bucket.wins / resolved) * 100)}%`;
}

export function SignalStatsCard() {
  const { data } = useSignalsStats();
  const scored = data?.by_score.reduce((sum, row) => sum + row.total, 0) ?? 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Confluence results</CardTitle>
        <CardDescription>
          Target hit before stop is a win. Same bar counts as a loss. Open trades stay pending.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!data || scored === 0 ? (
          <p className="text-xs text-muted-foreground">
            No scored signals yet. Turn on technical analysis and let the bot log a few alerts.
          </p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {data.by_score.map((row) => (
              <div key={row.score} className="flex items-center justify-between gap-3 text-sm">
                <span className="tabular-nums">Score {row.score}</span>
                <span className="text-muted-foreground tabular-nums">
                  {winRate(row)} · {row.wins}W {row.losses}L
                  {row.expired ? ` · ${row.expired} expired` : ""}
                  {row.pending ? ` · ${row.pending} pending` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
        {data && (data.sent.total > 0 || data.filtered.total > 0) && (
          <p className="text-xs text-muted-foreground">
            Sent {data.sent.total} ({winRate(data.sent)}) · Blocked {data.filtered.total} (
            {winRate(data.filtered)})
          </p>
        )}
      </CardContent>
    </Card>
  );
}
