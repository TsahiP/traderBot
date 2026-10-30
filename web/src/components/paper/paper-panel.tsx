"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { LiveFeed } from "@/components/paper/live-feed";
import { PaperMetrics } from "@/components/paper/metrics";
import { PaperPositions } from "@/components/paper/positions";
import { PaperProposals } from "@/components/paper/proposals";
import { PaperSetups } from "@/components/paper/setups";
import { StatusBar } from "@/components/paper/status-bar";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { usePaperStream } from "@/hooks/use-paper-stream";
import { describeEvent, px } from "@/lib/paper-format";
import type { PaperCommandKind, PaperEvent, PaperHistoryState } from "@/lib/schemas";

const TOAST_TYPES = new Set(["command", "fill", "trade.closed", "proposal.created", "position.adopted"]);

function toastFor(event: PaperEvent) {
  const { text, tone } = describeEvent(event);
  if (event.type === "fill" && event.payload.kind !== "entry") return;
  if (event.type === "command" && event.payload.status !== "error") {
    toast.success(text);
  } else if (tone === "down") {
    toast.error(text);
  } else if (tone === "up") {
    toast.success(text);
  } else {
    toast(text);
  }
}

function OfflineHistory({ history }: { history: PaperHistoryState | null }) {
  if (!history || history.empty) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          paperbot has not run yet. Start it with <code className="text-foreground">python paperbot.py</code> - this
          tab connects on its own.
        </CardContent>
      </Card>
    );
  }
  const hb = history.heartbeat;
  const trades = history.open_trades ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">While paperbot is offline</CardTitle>
        <CardDescription>
          From the journal (dashboard.py). Last heartbeat {hb?.ts ? hb.ts.replace("T", " ").slice(0, 19) : "never"} ·
          brackets at Alpaca keep working without the bot.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <div className="text-muted-foreground">
          {history.counters
            ? `${history.counters.entries_today} entries today · daily loss budget used $${history.counters.daily_loss_used.toFixed(2)}`
            : null}
          {history.proposals?.length ? ` · ${history.proposals.length} proposal(s) pending` : ""}
        </div>
        {trades.length === 0 ? (
          <div className="text-muted-foreground">No open trades on record.</div>
        ) : (
          trades.map((t) => (
            <div key={String(t.id)} className="font-mono text-xs">
              {String(t.symbol)} {String(t.side)} {String(t.qty)} @ {px(t.entry_price as number)} · stop{" "}
              {px(t.stop as number | null)} · target {px(t.target as number | null)} · {String(t.run_id)}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

export function PaperPanel() {
  const { snapshot, feed, connection, history, sendCommand } = usePaperStream();
  const [busy, setBusy] = useState(false);
  const seen = useRef<number | null>(null);
  const live = connection === "live" && snapshot != null;
  const disabled = live ? undefined : "paperbot is offline - start paperbot.py";

  // Toast only what happens from now on, not the backlog replayed on connect.
  useEffect(() => {
    if (!live) return;
    const newest = feed.reduce((max, e) => Math.max(max, e.id ?? 0), 0);
    if (seen.current === null) {
      seen.current = newest;
      return;
    }
    const baseline = seen.current;
    feed
      .filter((e) => (e.id ?? 0) > baseline && TOAST_TYPES.has(e.type))
      .reverse()
      .forEach(toastFor);
    seen.current = Math.max(baseline, newest);
  }, [feed, live]);

  const run = useCallback(
    async (kind: PaperCommandKind, args: Record<string, unknown> = {}) => {
      setBusy(true);
      try {
        await sendCommand(kind, args);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [sendCommand],
  );

  return (
    <div className="flex flex-col gap-4">
      <StatusBar snapshot={snapshot} connection={connection} busy={busy} onCommand={run} />

      {connection === "connecting" && !snapshot ? (
        <Card>
          <CardContent className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Spinner /> Connecting to paperbot…
          </CardContent>
        </Card>
      ) : !snapshot ? (
        <OfflineHistory history={history} />
      ) : (
        <>
          <PaperMetrics snapshot={snapshot} />
          <PaperProposals
            proposals={snapshot.proposals}
            manual={snapshot.config.approval_mode === "manual"}
            disabled={disabled}
            onApprove={(id) => run("approve", { proposal_id: id })}
            onReject={(id) => run("reject", { proposal_id: id })}
          />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="flex min-w-0 flex-col gap-4">
              <PaperPositions
                positions={snapshot.positions}
                disabled={disabled}
                onClose={(symbol) => run("close_position", { symbol })}
              />
              <PaperSetups setups={snapshot.setups} />
            </div>
            <LiveFeed feed={feed} live={live} />
          </div>
        </>
      )}
    </div>
  );
}
