"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  Minus,
  Radio,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { fmtNum } from "@/lib/format";
import type { LiveSnapshot } from "@/lib/schemas";

function SignalBadge({ signal }: { signal: number | undefined }) {
  if (signal === 1) {
    return (
      <Badge
        variant="outline"
        className="border-up/40 bg-up/10 text-up data-[slot=badge]:rounded-md"
      >
        <ArrowUpRight data-icon="inline-start" />
        BUY
      </Badge>
    );
  }
  if (signal === -1) {
    return (
      <Badge
        variant="outline"
        className="border-down/40 bg-down/10 text-down data-[slot=badge]:rounded-md"
      >
        <ArrowDownRight data-icon="inline-start" />
        SELL
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground data-[slot=badge]:rounded-md">
      <Minus data-icon="inline-start" />
      HOLD
    </Badge>
  );
}

function TapeItem({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
      <span className="tracking-wider uppercase">{label}</span>
      <b className={cn("text-sm font-semibold tabular-nums text-foreground", className)}>
        {children}
      </b>
    </span>
  );
}

export function TickerTape({ live }: { live: LiveSnapshot | undefined }) {
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prevClose = useRef<number | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const close = live?.close;
    if (close == null || prevClose.current === null) {
      prevClose.current = close ?? null;
      return;
    }
    if (close > prevClose.current) setFlash("up");
    else if (close < prevClose.current) setFlash("down");
    prevClose.current = close;
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 800);
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, [live?.close]);

  const liveConnected = Boolean(live?.connected);
  const marketOpen = Boolean(live?.market_open);

  return (
    <header className="sticky top-0 z-50 border-b bg-tape/95 backdrop-blur">
      <div className="mx-auto flex h-12 max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-1 px-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <span className="flex items-center gap-1.5 text-xs uppercase tracking-[0.18em] text-muted-foreground">
            <Radio
              data-icon="inline-start"
              className={cn(
                "size-3.5",
                liveConnected && marketOpen
                  ? "animate-pulse text-up"
                  : liveConnected
                    ? "text-up"
                    : "text-muted-foreground",
              )}
            />
            tradebot
          </span>
          <span className="font-semibold uppercase tracking-[0.3em]">
            {live?.symbol ?? "SPY"}
          </span>
        </div>

        <TapeItem
          label="last"
          className={cn(
            "transition-colors duration-300",
            flash === "up" && "text-up",
            flash === "down" && "text-down",
          )}
        >
          {live?.close != null ? fmtNum(live.close) : "—"}
        </TapeItem>
        <TapeItem label="sma-fast">
          {live?.sma_fast != null ? fmtNum(live.sma_fast) : "—"}
        </TapeItem>
        <TapeItem label="sma-slow">
          {live?.sma_slow != null ? fmtNum(live.sma_slow) : "—"}
        </TapeItem>

        <SignalBadge signal={live?.signal} />

        <div className="ml-auto hidden text-xs text-muted-foreground sm:block">
          {live?.fallback
            ? "backtest data (no .env keys)"
            : live?.connected
              ? marketOpen
                ? "market open"
                : "market closed"
              : live?.reason ?? "connecting…"}
        </div>
      </div>
    </header>
  );
}
