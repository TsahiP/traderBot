"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { PaperSetup, SetupStateName } from "@/lib/schemas";

const ORDER: SetupStateName[] = [
  "TRIGGERED",
  "PROPOSED",
  "WAITING",
  "ARMED",
  "ENTERED",
  "MANAGING",
  "IDLE",
  "BLOCKED",
  "EXPIRED",
  "EXITED",
];

const VARIANT: Partial<Record<SetupStateName, "default" | "secondary" | "destructive" | "outline">> = {
  TRIGGERED: "default",
  PROPOSED: "default",
  ENTERED: "default",
  MANAGING: "default",
  BLOCKED: "destructive",
  WAITING: "outline",
  ARMED: "outline",
};

export function PaperSetups({ setups }: { setups: PaperSetup[] }) {
  const rows = [...setups].sort(
    (a, b) =>
      ORDER.indexOf(a.state) - ORDER.indexOf(b.state) ||
      Math.abs(a.distance_pct ?? Infinity) - Math.abs(b.distance_pct ?? Infinity),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Waiting for</CardTitle>
        <CardDescription>What each run is watching, and why it did or did not act</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No setups yet today - swing runs evaluate after the close.
          </p>
        ) : (
          <div className="flex flex-col">
            {rows.map((s) => (
              <div
                key={`${s.run_id}|${s.symbol}|${s.day}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t py-2 text-sm first:border-t-0 first:pt-0"
              >
                <span className="w-16 font-semibold">{s.symbol}</span>
                <Badge variant={VARIANT[s.state] ?? "secondary"} className="w-24 justify-center">
                  {s.state}
                </Badge>
                <span className="text-xs text-muted-foreground">{s.run_id}</span>
                <span className="min-w-0 flex-1 text-xs">{s.sentence}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
