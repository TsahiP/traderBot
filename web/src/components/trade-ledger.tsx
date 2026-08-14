"use client";

import { History, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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
import type { Trade } from "@/lib/schemas";

function SourceBadge({ source }: { source: Trade["source"] }) {
  return (
    <Badge
      variant={source === "live" ? "default" : "secondary"}
      className={cn(
        "data-[slot=badge]:rounded-md",
        source === "live" && "uppercase tracking-wider",
      )}
    >
      {source}
    </Badge>
  );
}

export function TradeLedger({
  trades,
  loading,
}: {
  trades: Trade[] | undefined;
  loading?: boolean;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Closed</TableHead>
          <TableHead>Opened</TableHead>
          <TableHead className="text-right">Buy</TableHead>
          <TableHead className="text-right">Sell</TableHead>
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">P&L</TableHead>
          <TableHead className="w-24 text-right">Source</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {loading && !trades?.length ? (
          <TableRow>
            <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
              <Loader2 className="mx-auto size-4 animate-spin" />
            </TableCell>
          </TableRow>
        ) : !trades?.length ? (
          <TableRow>
            <TableCell
              colSpan={7}
              className="h-24 text-center text-muted-foreground"
            >
              No trades recorded yet. Backtest results appear here after running
              backtest.py; paper trades appear as the bot closes them.
            </TableCell>
          </TableRow>
        ) : (
          trades.map((t, i) => (
            <TableRow key={`${t.exit_date}-${i}`}>
              <TableCell className="tabular-nums">{t.exit_date}</TableCell>
              <TableCell className="tabular-nums text-muted-foreground">
                {t.entry_date}
              </TableCell>
              <TableCell className="tabular-nums text-right">
                {fmtUsd(t.entry_price)}
              </TableCell>
              <TableCell className="tabular-nums text-right">
                {fmtUsd(t.exit_price)}
              </TableCell>
              <TableCell className="tabular-nums text-right">{t.qty}</TableCell>
              <TableCell
                className={cn("tabular-nums text-right font-semibold", pnlClass(t.pnl))}
              >
                {fmtSignedUsd(t.pnl)}
              </TableCell>
              <TableCell className="text-right">
                <SourceBadge source={t.source} />
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}

export function LedgerEmptyHint() {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
      <History className="size-5" />
      <p className="text-sm">
        Ledger is empty — it fills up as backtests and paper trades complete.
      </p>
    </div>
  );
}
