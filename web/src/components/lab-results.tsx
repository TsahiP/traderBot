"use client";

import { TriangleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { MetricsStrip } from "@/components/metrics-strip";
import { LlmAnalysis } from "@/components/llm-analysis";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { fmtSignedUsd, fmtUsd, pnlClass } from "@/lib/format";
import type { BacktestRun } from "@/lib/schemas";
import type { RunParams } from "@/hooks/use-backtest-run";

export function RunError({ message }: { message: string }) {
  return (
    <Alert variant="destructive">
      <TriangleAlert data-icon="inline-start" />
      <AlertTitle>Backtest failed</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

export function LabResults({
  data,
  params,
}: {
  data: BacktestRun;
  params: RunParams | null;
}) {
  const { metrics, trades, meta } = data;
  return (
    <div className="flex flex-col">
      <MetricsStrip metrics={metrics} />
      <LlmAnalysis params={params} />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Closed</TableHead>
            <TableHead>Opened</TableHead>
            <TableHead>Side</TableHead>
            <TableHead className="text-right">Buy</TableHead>
            <TableHead className="text-right">Sell</TableHead>
            <TableHead className="text-right">P&L</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {trades.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={6}
                className="h-20 text-center text-muted-foreground"
              >
                No trades — {meta.strategy_label} never fired on {meta.symbol} ({meta.timeframe}) in this window.
              </TableCell>
            </TableRow>
          ) : (
            trades
              .slice()
              .reverse()
              .map((t, i) => (
                <TableRow key={`${t.exit_date}-${i}`}>
                  <TableCell className="tabular-nums">
                    {t.exit_date ?? "—"}
                  </TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">
                    {t.entry_date}
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                          t.side === "long"
                            ? "bg-up/10 text-up"
                            : "bg-down/10 text-down",
                        )}
                      >
                        {t.side}
                      </span>
                      {t.exit_type === "eod" && (
                        <span className="rounded bg-amber/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber">
                          EOD
                        </span>
                      )}
                      {t.exit_type === "open" && (
                        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                          OPEN
                        </span>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="tabular-nums text-right">
                    {fmtUsd(t.entry_price)}
                  </TableCell>
                  <TableCell className="tabular-nums text-right">
                    {t.exit_price != null ? fmtUsd(t.exit_price) : "—"}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "tabular-nums text-right font-semibold",
                      pnlClass(t.pnl),
                    )}
                  >
                    {fmtSignedUsd(t.pnl)}
                    {t.exit_type === "open" ? " *" : ""}
                  </TableCell>
                </TableRow>
              ))
          )}
        </TableBody>
      </Table>
      <p className="border-t px-4 py-2 text-[11px] leading-relaxed text-muted-foreground">
        Every executed fill is a marker on the chart; one table row is one
        closed round trip (entry + exit). <b className="text-amber">EOD</b> = forced
        session-end close, <b className="text-primary">OPEN</b> = position still
        held at the end of the data (P&L unrealized, marked *).
      </p>
    </div>
  );
}
