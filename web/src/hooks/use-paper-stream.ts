"use client";

import { useCallback, useEffect, useState } from "react";

import { HttpError } from "@/lib/api";
import {
  applyEvent,
  emptyView,
  fromSnapshot,
  needsRefresh,
  type PaperView,
} from "@/lib/paper-reducer";
import {
  PaperCommandResponse,
  PaperEvent,
  PaperHistoryState,
  PaperStateResponse,
  type PaperCommandKind,
} from "@/lib/schemas";

/* Live link to paperbot: GET /state for the snapshot, then an EventSource on
   /stream that applies each event as it arrives. When the stream drops, the
   hook reconnects from scratch - a fresh snapshot, then the stream from just
   before it (the last events refill the feed) - so nothing is missed even if
   the bot restarted in between. While the bot is unreachable the
   desk shows history from dashboard.py (/api/paper/state) and keeps retrying. */

export const PAPERBOT_URL = process.env.NEXT_PUBLIC_PAPERBOT_URL ?? "http://127.0.0.1:8100";
const RETRY_MS = 5_000;
const REFRESH_DEBOUNCE_MS = 300;
const BACKLOG_EVENTS = 50;

export type PaperConnection = "connecting" | "live" | "offline";

export function usePaperStream() {
  const [view, setView] = useState<PaperView>(emptyView);
  const [connection, setConnection] = useState<PaperConnection>("connecting");
  const [history, setHistory] = useState<PaperHistoryState | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    let stopped = false;
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    async function fetchState(fresh = false): Promise<number | null> {
      try {
        const res = await fetch(`${PAPERBOT_URL}/state`, { cache: "no-store" });
        if (!res.ok) return null;
        const parsed = PaperStateResponse.safeParse(await res.json());
        if (!parsed.success) {
          console.warn("paperbot /state did not match the schema", parsed.error.issues);
          return null;
        }
        if ("starting" in parsed.data) return 0;
        const snapshot = parsed.data;
        if (!stopped) setView((v) => fromSnapshot(fresh ? emptyView() : v, snapshot));
        return snapshot.last_event_id;
      } catch {
        return null;
      }
    }

    async function loadHistory() {
      try {
        const res = await fetch("/api/paper/state", { cache: "no-store" });
        const parsed = PaperHistoryState.safeParse(await res.json());
        if (parsed.success && !stopped) setHistory(parsed.data);
      } catch {
        /* dashboard.py down too: nothing to show */
      }
    }

    function scheduleRefresh() {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => void fetchState(), REFRESH_DEBOUNCE_MS);
    }

    async function connect() {
      if (stopped) return;
      const lastId = await fetchState(true);
      if (stopped) return;
      if (lastId === null) {
        setConnection("offline");
        void loadHistory();
        retryTimer = setTimeout(() => void connect(), RETRY_MS);
        return;
      }
      const after = Math.max(0, lastId - BACKLOG_EVENTS);
      source = new EventSource(`${PAPERBOT_URL}/stream?after=${after}`);
      source.onopen = () => setConnection("live");
      source.onmessage = (msg) => {
        let raw: unknown;
        try {
          raw = JSON.parse(msg.data);
        } catch {
          return;
        }
        const parsed = PaperEvent.safeParse(raw);
        if (!parsed.success) return;
        const event = parsed.data;
        setView((v) => applyEvent(v, event));
        if (needsRefresh(event)) scheduleRefresh();
      };
      source.onerror = () => {
        // Start clean instead of letting the browser resume with an old Last-Event-ID:
        // the bot may have restarted (or its journal been replaced) in between.
        source?.close();
        source = null;
        setConnection("offline");
        void loadHistory();
        retryTimer = setTimeout(() => void connect(), RETRY_MS);
      };
    }

    void connect();
    return () => {
      stopped = true;
      source?.close();
      clearTimeout(retryTimer);
      clearTimeout(refreshTimer);
    };
  }, [refreshTick]);

  /** Tear down and reconnect (e.g. a "retry now" button). */
  const reconnect = useCallback(() => setRefreshTick((n) => n + 1), []);

  const sendCommand = useCallback(
    async (kind: PaperCommandKind, args: Record<string, unknown> = {}): Promise<PaperCommandResponse> => {
      let res: Response;
      try {
        res = await fetch(`${PAPERBOT_URL}/commands`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind, args }),
        });
      } catch {
        throw new HttpError(0, "paperbot unreachable - is paperbot.py running?");
      }
      const body: unknown = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message =
          body && typeof body === "object" && "error" in body && typeof body.error === "string"
            ? body.error
            : `Command failed (${res.status})`;
        throw new HttpError(res.status, message);
      }
      return PaperCommandResponse.parse(body);
    },
    [],
  );

  return {
    snapshot: view.snapshot,
    feed: view.feed,
    lastEventId: view.lastEventId,
    connection,
    history,
    sendCommand,
    reconnect,
  };
}
