"use client";

import useSWR from "swr";

import { apiFetcher, HttpError } from "@/lib/api";
import {
  ApiError,
  FundiNewsResponse,
  FundiRefreshResponse,
  FundiResponse,
} from "@/lib/schemas";

export function useFundi() {
  return useSWR<FundiResponse>("/api/fundi", (url: string) =>
    apiFetcher(url, FundiResponse),
  );
}

export function useFundiNews(limit = 40) {
  return useSWR<FundiNewsResponse>(
    `/api/fundi/news?limit=${limit}`,
    (url: string) => apiFetcher(url, FundiNewsResponse),
    { refreshInterval: 60_000 },
  );
}

export async function fetchFundiRefresh(symbol: string): Promise<FundiRefreshResponse> {
  let res: Response;
  try {
    res = await fetch("/api/fundi/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, force: true }),
    });
  } catch {
    throw new HttpError(0, "API unreachable - is dashboard.py running?");
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      message = ApiError.parse(await res.json()).error;
    } catch {
      /* keep default message */
    }
    throw new HttpError(res.status, message);
  }
  return (await res.json()) as FundiRefreshResponse;
}
