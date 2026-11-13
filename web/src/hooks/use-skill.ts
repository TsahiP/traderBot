"use client";

import useSWR from "swr";

import { apiFetcher, HttpError } from "@/lib/api";
import { ApiError, SkillResponse } from "@/lib/schemas";

export function useSkill() {
  return useSWR<SkillResponse>(
    "/api/skill",
    (url: string) => apiFetcher(url, SkillResponse),
    { refreshInterval: (data) => (data?.running ? 2_000 : 20_000) },
  );
}

async function post(url: string, body?: object): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
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

export function checkSkill(): Promise<void> {
  return post("/api/skill/run");
}
