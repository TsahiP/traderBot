"use client";

import useSWR from "swr";

import { HttpError, apiFetcher } from "@/lib/api";
import {
  ApiError,
  SignalsConfig,
  SignalsConfigResponse,
  SignalsHistoryResponse,
  SignalsStatus,
} from "@/lib/schemas";

export function useSignalsConfig() {
  return useSWR<SignalsConfigResponse>("/api/signals/config", (url: string) =>
    apiFetcher(url, SignalsConfigResponse),
  );
}

export function useSignalsStatus() {
  return useSWR<SignalsStatus>(
    "/api/signals/status",
    (url: string) => apiFetcher(url, SignalsStatus),
    { refreshInterval: 30_000 },
  );
}

export function useSignalsHistory(limit = 50) {
  const key = `/api/signals/history?limit=${limit}`;
  return useSWR<SignalsHistoryResponse>(
    key,
    (url: string) => apiFetcher(url, SignalsHistoryResponse),
    { refreshInterval: 30_000 },
  );
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new HttpError(0, "API unreachable - is dashboard.py running?");
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const parsed = ApiError.parse(await res.json());
      message = parsed.error;
    } catch {
      /* keep default message */
    }
    throw new HttpError(res.status, message);
  }
  return (await res.json()) as T;
}

export function saveSignalsConfig(cfg: SignalsConfig) {
  return postJson<SignalsConfig>("/api/signals/config", cfg);
}

export function sendSignalTest() {
  return postJson<{ ok: boolean }>("/api/signals/test", {});
}
