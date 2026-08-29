"use client";

import useSWR from "swr";

import { apiFetcher } from "@/lib/api";
import {
  CryptoOverviewResponse,
  EquityResponse,
  LiveSnapshot,
  Stats,
  StockAnalysisResponse,
  StrategiesResponse,
  TradesResponse,
  WatchlistResponse,
} from "@/lib/schemas";

const REFRESH = 30_000;

export function useLive() {
  return useSWR<LiveSnapshot>("/api/live", (url: string) =>
    apiFetcher(url, LiveSnapshot),
  );
}

export function useStats() {
  return useSWR<Stats>("/api/stats", (url: string) => apiFetcher(url, Stats), {
    refreshInterval: REFRESH,
  });
}

export function useTrades() {
  return useSWR<TradesResponse>("/api/trades", (url: string) =>
    apiFetcher(url, TradesResponse),
  );
}

export function useEquity() {
  return useSWR<EquityResponse>("/api/equity", (url: string) =>
    apiFetcher(url, EquityResponse),
  );
}

export function useStrategies() {
  return useSWR<StrategiesResponse>("/api/strategies", (url: string) =>
    apiFetcher(url, StrategiesResponse),
  );
}

export function useWatchlist() {
  return useSWR<WatchlistResponse>("/api/watchlist", (url: string) =>
    apiFetcher(url, WatchlistResponse),
  );
}

export function useStockAnalysis(symbol: string | null) {
  const sym = symbol?.trim().toUpperCase();
  return useSWR<StockAnalysisResponse>(
    sym ? `/api/stock-analysis?symbol=${encodeURIComponent(sym)}` : null,
    (url: string) => apiFetcher(url, StockAnalysisResponse),
    { revalidateOnFocus: false },
  );
}

export function useCryptoOverview() {
  return useSWR<CryptoOverviewResponse>(
    "/api/crypto-analysis",
    (url: string) => apiFetcher(url, CryptoOverviewResponse),
    { refreshInterval: 60_000, revalidateOnFocus: false },
  );
}
