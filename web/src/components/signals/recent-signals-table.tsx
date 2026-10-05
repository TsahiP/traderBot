"use client";

import { useState } from "react";
import { BellRing } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { SignalEntry } from "@/lib/schemas";

function resultOf(signal: SignalEntry) {
  if (signal.outcome === "win") return { text: "Win", className: "text-up" };
  if (signal.outcome === "loss") return { text: "Loss", className: "text-down" };
  if (signal.outcome === "expired") return { text: "Expired", className: "text-muted-foreground" };
  if (signal.confluence) return { text: "Pending", className: "text-muted-foreground" };
  return { text: "—", className: "text-muted-foreground" };
}

function ScoreCell({ signal, showDetails }: { signal: SignalEntry; showDetails: boolean }) {
  const confluence = signal.confluence;
  if (!confluence) return <span className="text-muted-foreground">—</span>;
  const score = (
    <span className="tabular-nums">
      {confluence.score}/{confluence.total}
    </span>
  );
  if (!showDetails) return score;
  return (
    <Tooltip>
      <TooltipTrigger className="underline decoration-dotted underline-offset-2">
        {score}
      </TooltipTrigger>
      <TooltipContent>
        <ul className="flex flex-col gap-0.5 text-left">
          {confluence.checks.map((check) => (
            <li key={check.id}>
              {check.passed == null ? "~" : check.passed ? "+" : "−"} {check.label}: {check.detail}
            </li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}

export function RecentSignalsTable({ signals }: { signals: SignalEntry[] }) {
  const [showDetails, setShowDetails] = useState(true);

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-sm">Recent signals</CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Details</span>
            <Switch size="sm" checked={showDetails} onCheckedChange={setShowDetails} />
          </div>
        </div>
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
          <TooltipProvider>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>List</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>TF</TableHead>
                <TableHead>Pattern</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Result</TableHead>
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
                  <TableCell>
                    <ScoreCell signal={s} showDetails={showDetails} />
                  </TableCell>
                  <TableCell className={resultOf(s).className}>{resultOf(s).text}</TableCell>
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
          </TooltipProvider>
        )}
      </CardContent>
    </Card>
  );
}
