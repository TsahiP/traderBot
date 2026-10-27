"use client";

import useSWR from "swr";

import { apiFetcher, HttpError } from "@/lib/api";
import { ApiError, ScannerResponse } from "@/lib/schemas";

export function useScanner() {
  return useSWR<ScannerResponse>(
    "/api/scanner",
    (url: string) => apiFetcher(url, ScannerResponse),
    // poll fast while a scan runs, slowly otherwise
    { refreshInterval: (data) => (data?.running ? 3_000 : 60_000) },
  );
}

export async function startScan(mode: "daily" | "orb", scope: "watchlists" | "universe"): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/scanner/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, scope }),
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
}
