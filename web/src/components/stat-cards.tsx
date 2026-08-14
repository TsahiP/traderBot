"use client";

import { Layers, PiggyBank, Target, Wallet } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  fmtNum,
  fmtPct,
  fmtSignedUsd,
  fmtUsd,
  pnlClass,
} from "@/lib/format";
import type { LiveSnapshot, Stats } from "@/lib/schemas";

function StatCard({
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
          <span className="grid size-6 place-items-center rounded-md bg-muted text-foreground">
            {icon}
          </span>
          <span className="text-[11px] font-medium uppercase tracking-[0.14em]">
            {label}
          </span>
        </div>
        <div className={cn("text-2xl font-bold tabular-nums leading-none", valueClass)}>
          {value}
        </div>
        <div className="text-xs leading-relaxed text-muted-foreground">{sub}</div>
      </CardContent>
    </Card>
  );
}

export function StatCards({ stats, live }: { stats?: Stats; live?: LiveSnapshot }) {
  const account = live?.account;
  const position = live?.position;
  const realized = stats?.realized;
  const bt = stats?.backtest;

  const winRate = realized?.win_rate ?? bt?.win_rate;
  const winSource = realized?.win_rate != null
    ? `${realized.trades} live trades`
    : bt?.win_rate != null
      ? `backtest · ${bt.trades} trades`
      : null;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard
        icon={<Wallet />}
        label="Account equity"
        value={account ? fmtUsd(account.equity) : bt ? fmtUsd(bt.final_equity) : "—"}
        valueClass={account ? "text-primary" : undefined}
        sub={
          account
            ? `cash ${fmtUsd(account.cash)} · buying power ${fmtUsd(account.buying_power)}`
            : bt
              ? `backtest final ${bt.start} → ${bt.end}`
              : "no backtest output yet"
        }
      />
      <StatCard
        icon={<Layers />}
        label="Position"
        value={position ? `${position.qty} × ${fmtNum(position.avg_entry)}` : "flat"}
        sub={
          position ? (
            <span>
              MV {fmtUsd(position.market_value)} ·{" "}
              <span className={pnlClass(position.unrealized_pl)}>
                {fmtSignedUsd(position.unrealized_pl)}{" "}
                ({fmtPct(position.unrealized_pl_pct)})
              </span>
            </span>
          ) : live?.connected ? (
            "no open position"
          ) : (
            "add Alpaca keys in .env to connect"
          )
        }
      />
      <StatCard
        icon={<PiggyBank />}
        label="Realized P&L"
        value={realized ? fmtSignedUsd(realized.total_pnl) : "—"}
        valueClass={realized ? pnlClass(realized.total_pnl) : undefined}
        sub={
          realized
            ? `${realized.trades} ${realized.trades === 1 ? "closed trade" : "closed trades"} · ${realized.wins} wins`
            : "no closed live trades"
        }
      />
      <StatCard
        icon={<Target />}
        label="Win rate"
        value={winRate != null ? `${(winRate * 100).toFixed(1)}%` : "—"}
        valueClass={winRate != null ? "text-up" : undefined}
        sub={winSource ?? "run a backtest to see it"}
      />
    </div>
  );
}
