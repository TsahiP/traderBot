"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Play, ScanSearch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtUsd, fmtUsd0 } from "@/lib/format";
import { startScan, useScanner } from "@/hooks/use-scanner";

const SETUP_LABEL: Record<string, string> = {
  breakout_20d: "20d breakout",
  bull_flag: "bull flag",
  reclaim_ma20: "reclaim MA20",
  oversold: "oversold",
  oversold_bounce: "oversold bounce",
  orb: "ORB",
};

type Mode = "daily" | "orb";
type Scope = "watchlists" | "universe";

export function ScannerPanel() {
  const { data, error, mutate } = useScanner();
  const [mode, setMode] = useState<Mode>("daily");
  const [scope, setScope] = useState<Scope>("watchlists");
  const running = Boolean(data?.running);
  const results = data?.results;

  async function run() {
    try {
      await startScan(mode, scope);
      toast.success(scope === "universe" ? "Universe scan started (a few minutes)" : "Scan started");
      await mutate();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle className="text-sm">Market scanner</CardTitle>
          <CardDescription>
            {results
              ? `${results.mode} · ${results.scope} · ${results.hits.length} hits from ${results.with_data}/${results.requested} symbols · ${results.ts.slice(0, 16).replace("T", " ")} NY · ${results.seconds}s`
              : "Daily setups (20d breakout, bull flag, MA20 reclaim, oversold) or opening-range breakouts."}
          </CardDescription>
          {data?.error && <p className="text-xs text-down">Last scan failed: {data.error}</p>}
          {error && <p className="text-xs text-down">{error.message}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={mode}
            items={{ daily: "Daily setups", orb: "Opening range" }}
            onValueChange={(v) => setMode(v as Mode)}
          >
            <SelectTrigger size="sm" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="daily">Daily setups</SelectItem>
              <SelectItem value="orb">Opening range</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={scope}
            items={{ watchlists: "My watchlists", universe: "All US stocks" }}
            onValueChange={(v) => setScope(v as Scope)}
          >
            <SelectTrigger size="sm" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="watchlists">My watchlists</SelectItem>
              <SelectItem value="universe">All US stocks</SelectItem>
            </SelectContent>
          </Select>
          <Button size="sm" onClick={run} disabled={running}>
            {running ? <Spinner data-icon="inline-start" /> : <Play data-icon="inline-start" />}
            {running ? "Scanning…" : "Run scan"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {!results || results.hits.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ScanSearch />
              </EmptyMedia>
              <EmptyTitle>
                {results?.note ? "Opening-range scan not ready" : results ? "No setups on the last scan" : "No scan yet"}
              </EmptyTitle>
              <EmptyDescription>
                {results?.note ? (
                  <>{results.note}.</>
                ) : (
                  <>
                    Run a scan here or from a terminal with{" "}
                    <code className="font-mono">python scanner.py daily --scope universe</code>.
                    Daily scans fit best before the open or after the close.
                  </>
                )}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Setups</TableHead>
                <TableHead className="text-right">Close</TableHead>
                <TableHead className="text-right">{results.mode === "orb" ? "Range high" : "Chg"}</TableHead>
                <TableHead className="text-right">{results.mode === "orb" ? "At" : "RSI"}</TableHead>
                <TableHead className="text-right">$ volume</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {results.hits.map((h) => (
                <TableRow key={h.symbol}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-semibold">{h.symbol}</span>
                      {h.name && (
                        <span className="max-w-56 truncate text-xs text-muted-foreground">{h.name}</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {h.setups.map((s) => (
                        <Badge key={s} variant="secondary">
                          {SETUP_LABEL[s] ?? s}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{fmtUsd(h.close)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {results.mode === "orb" ? (
                      h.range_high != null ? fmtUsd(h.range_high) : "—"
                    ) : h.change_pct != null ? (
                      <span className={h.change_pct >= 0 ? "text-up" : "text-down"}>
                        {h.change_pct >= 0 ? "+" : ""}
                        {h.change_pct.toFixed(2)}%
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {results.mode === "orb" ? (h.at ?? "—") : h.rsi != null ? h.rsi.toFixed(1) : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{fmtUsd0(h.dollar_volume)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
