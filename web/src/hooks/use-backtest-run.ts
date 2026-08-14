"use client";

import { useCallback, useState } from "react";
import useSWR from "swr";

import { apiFetcher } from "@/lib/api";
import { BacktestRun } from "@/lib/schemas";
import type { LabValues } from "@/lib/schemas";

export interface RunParams {
  symbol: string;
  strategy: string;
  timeframe: string;
  start?: string;
  end?: string;
  qty: number;
  capital: number;
  allow_short: boolean;
  cost_per_share: number;
  params: Record<string, number>;
}

function toKey(p: RunParams): string {
  const qs = new URLSearchParams({
    symbol: p.symbol,
    strategy: p.strategy,
    timeframe: p.timeframe,
    qty: String(p.qty),
    capital: String(p.capital),
    allow_short: String(p.allow_short),
    cost_per_share: String(p.cost_per_share),
  });
  if (p.start) qs.set("start", p.start);
  if (p.end) qs.set("end", p.end);
  for (const [k, v] of Object.entries(p.params)) {
    qs.set(k, String(v));
  }
  return `/api/backtest/run?${qs.toString()}`;
}

export function useBacktestRun() {
  const [params, setParams] = useState<RunParams | null>(null);

  const key = params ? toKey(params) : null;
  const { data, error, isValidating } = useSWR<BacktestRun>(
    key,
    (url: string) => apiFetcher(url, BacktestRun),
  );

  const run = useCallback((values: LabValues) => {
    const {
      symbol,
      strategy,
      timeframe,
      start,
      end,
      qty,
      capital,
      allow_short,
      cost_per_share,
    } = values;
    const EXCLUDED = [
      "symbol",
      "strategy",
      "timeframe",
      "start",
      "end",
      "qty",
      "capital",
      "allow_short",
      "cost_per_share",
    ];
    const params: Record<string, number> = {};
    for (const [k, v] of Object.entries(values)) {
      if (!EXCLUDED.includes(k)) params[k] = Number(v);
    }
    setParams({
      symbol,
      strategy,
      timeframe,
      start: start || undefined,
      end: end || undefined,
      qty,
      capital,
      allow_short,
      cost_per_share,
      params,
    });
  }, []);

  return {
    run,
    params,
    data,
    error: error as Error | undefined,
    isLoading: Boolean(key) && !data && !error,
    isValidating,
  };
}