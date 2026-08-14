"use client";

import useSWR from "swr";

import { apiFetcher } from "@/lib/api";
import {
  EquityResponse,
  LiveSnapshot,
  Stats,
  StrategiesResponse,
  TradesResponse,
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
