"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Timer } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { checkSkill, useSkill } from "@/hooks/use-skill";
import { fmtUsd } from "@/lib/format";

function money(n: number | null): string {
  return n == null ? "—" : fmtUsd(n);
}

export function SkillPanel() {
  const { data, mutate } = useSkill();
  const [busy, setBusy] = useState<string | null>(null);
  const board = data?.board;
  const running = data?.running ?? false;

  async function runCheck() {
    setBusy("check");
    try {
      await checkSkill();
      toast("Checking the opening range");
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Check failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Day skill</CardTitle>
        <CardDescription>
          {board?.rule ??
            "The scanner runs first. After 9:50 only those names are checked for an opening-range breakout. A passing row is sent on its own."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={runCheck} disabled={running || busy !== null}>
            {running || busy === "check" ? <Spinner data-icon="inline-start" /> : <Timer data-icon="inline-start" />}
            Run scanner
          </Button>
          {board && (
            <span className="text-xs text-muted-foreground">
              {board.scan === "daily" ? "daily setups" : "opening range"} · {board.symbols} from the scanner · {board.ts}
            </span>
          )}
        </div>
        {data?.account_error && (
          <p className="text-xs text-muted-foreground">{data.account_error}</p>
        )}
        {board?.note && <p className="text-xs text-muted-foreground">{board.note}</p>}
        {data?.error && <p className="text-sm text-destructive">{data.error}</p>}
        {running && !board ? (
          <p className="text-sm text-muted-foreground">Scanning. The table shows up as soon as the list is ready.</p>
        ) : !board ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Timer />
              </EmptyMedia>
              <EmptyTitle>No scan yet</EmptyTitle>
              <EmptyDescription>
                Starts with the US daily scan, then the opening-range check on those names. The paper desk stays as it is.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Stop</TableHead>
                <TableHead className="text-right">Target</TableHead>
                <TableHead className="text-right">Shares</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {board.rows.map((row) => (
                <TableRow key={row.symbol}>
                  <TableCell className="font-semibold">{row.symbol}</TableCell>
                  <TableCell>
                    <div className="flex max-w-md flex-col gap-1">
                      <div className="flex flex-wrap gap-1">
                        <Badge variant="secondary">{row.status}</Badge>
                        {row.setups.map((setup) => (
                          <Badge key={setup} variant="outline">{setup}</Badge>
                        ))}
                      </div>
                      <span className="text-xs text-muted-foreground">{row.sentence}</span>
                      {row.verdict_reason && (
                        <span className="text-xs">{row.verdict}: {row.verdict_reason}</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.entry)}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.stop)}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.target)}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.qty ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
