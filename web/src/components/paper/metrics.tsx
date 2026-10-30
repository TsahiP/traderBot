"use client";

import { Gauge, ListChecks, ShieldAlert, Wallet } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { fmtUsd, pnlClass } from "@/lib/format";
import { signedR, signedUsd } from "@/lib/paper-format";
import type { PaperSnapshot } from "@/lib/schemas";
import { cn } from "@/lib/utils";

function Metric({
  icon,
  label,
  value,
  sub,
  valueClass,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub: React.ReactNode;
  valueClass?: string;
}) {
  return (
    <Card className="overflow-hidden">
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <span className="grid size-6 place-items-center rounded-md bg-muted text-foreground">{icon}</span>
          <span className="text-[11px] font-medium uppercase tracking-[0.14em]">{label}</span>
        </div>
        <div className={cn("text-2xl font-bold tabular-nums leading-none", valueClass)}>{value}</div>
        <div className="text-xs leading-relaxed text-muted-foreground">{sub}</div>
      </CardContent>
    </Card>
  );
}

const limit = (used: number, max: number | null, money = false) =>
  max == null ? (money ? fmtUsd(used) : String(used)) : `${money ? fmtUsd(used) : used} / ${money ? fmtUsd(max) : max}`;

export function PaperMetrics({ snapshot }: { snapshot: PaperSnapshot | null }) {
  const account = snapshot?.account;
  const positions = snapshot?.positions ?? [];
  const openPnl = positions.reduce((sum, p) => sum + p.pnl, 0);
  const rs = positions.map((p) => p.r).filter((r): r is number => r != null);
  const openR = rs.length ? rs.reduce((a, b) => a + b, 0) : null;
  const c = snapshot?.counters;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric
        icon={<Wallet />}
        label="Equity"
        value={account ? fmtUsd(account.equity) : "—"}
        valueClass={account ? "text-primary" : undefined}
        sub={account ? `cash ${fmtUsd(account.cash)} · buying power ${fmtUsd(account.buying_power)}` : "waiting for the bot"}
      />
      <Metric
        icon={<Gauge />}
        label="Open P&L"
        value={positions.length ? signedUsd(openPnl) : "flat"}
        valueClass={positions.length ? pnlClass(openPnl) : undefined}
        sub={positions.length ? `${positions.length} open${openR != null ? ` · ${signedR(openR)}` : ""}` : "no open positions"}
      />
      <Metric
        icon={<ShieldAlert />}
        label="Open risk"
        value={c ? limit(c.open_risk_usd, c.max_open_risk_usd, true) : "—"}
        sub="max loss at the stops of open trades"
      />
      <Metric
        icon={<ListChecks />}
        label="Entries today"
        value={c ? limit(c.entries_today, c.max_trades_per_day) : "—"}
        sub={c ? `daily loss budget used ${limit(c.daily_loss_used, c.max_daily_loss_usd, true)}` : ""}
      />
    </div>
  );
}
