"use client";

import { BellRing } from "lucide-react";

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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { SignalEntry } from "@/lib/schemas";

export function RecentSignalsTable({ signals }: { signals: SignalEntry[] }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Recent signals</CardTitle>
        <CardDescription>Every alert the bot has sent - newest first</CardDescription>
      </CardHeader>
      <CardContent>
        {signals.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <BellRing />
              </EmptyMedia>
              <EmptyTitle>No signals yet</EmptyTitle>
              <EmptyDescription>
                When a watched pattern completes on the last closed bar, the
                bot sends an alert to Telegram and it shows up here. Run{" "}
                <code className="font-mono">signalbot.py</code> in its own
                terminal to start listening.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>List</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>TF</TableHead>
                <TableHead>Pattern</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Close</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Stop</TableHead>
                <TableHead className="text-right">Target</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {signals.map((s) => (
                <TableRow key={s.key}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {new Date(s.ts).toLocaleString()}
                  </TableCell>
                  <TableCell>{s.list ?? "—"}</TableCell>
                  <TableCell className="font-mono font-medium">{s.symbol}</TableCell>
                  <TableCell>{s.timeframe}</TableCell>
                  <TableCell>{s.label}</TableCell>
                  <TableCell className={s.direction === "long" ? "text-up" : "text-down"}>
                    {s.direction === "long" ? "LONG" : "SHORT"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    ${s.close.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    ${s.entry.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {s.stop != null ? `$${s.stop.toFixed(2)}` : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {s.target != null ? `$${s.target.toFixed(2)}` : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
