"use client";

import { AlertTriangle, Pause, Play } from "lucide-react";

import { ConfirmButton } from "@/components/paper/confirm-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useNow } from "@/hooks/use-now";
import type { PaperConnection } from "@/hooks/use-paper-stream";
import type { PaperSnapshot } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const nyClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function Dot({ className }: { className: string }) {
  return <span className={cn("inline-block size-2 rounded-full", className)} />;
}

function ConnectionBadge({ connection }: { connection: PaperConnection }) {
  if (connection === "live") {
    return (
      <Badge variant="outline" className="gap-1.5">
        <Dot className="bg-up" /> Bot online
      </Badge>
    );
  }
  if (connection === "connecting") {
    return (
      <Badge variant="outline" className="gap-1.5">
        <Dot className="animate-pulse bg-muted-foreground" /> Connecting…
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="gap-1.5">
      <Dot className="bg-destructive" /> Bot offline
    </Badge>
  );
}

function StreamChip({ name, status }: { name: string; status: string }) {
  const tone =
    status === "up" ? "bg-up" : status === "idle" ? "bg-muted-foreground/50" : status === "down" ? "bg-down" : "bg-amber-500";
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" title={`${name} stream: ${status}`}>
      <Dot className={tone} />
      {name}
    </span>
  );
}

export function StatusBar({
  snapshot,
  connection,
  busy,
  onCommand,
}: {
  snapshot: PaperSnapshot | null;
  connection: PaperConnection;
  busy: boolean;
  onCommand: (kind: "pause" | "resume" | "flatten_all" | "set_approval", args?: Record<string, unknown>) => void;
}) {
  const now = useNow();
  const live = connection === "live" && snapshot != null;
  const offlineReason = live ? undefined : "paperbot is offline - start paperbot.py";
  const manual = snapshot?.config.approval_mode === "manual";
  const paused = Boolean(snapshot?.paused);

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <ConnectionBadge connection={connection} />
          <Badge variant="secondary" className="tabular-nums">
            {snapshot?.market === "open" ? "Market open" : "Market closed"}
            {now != null ? ` · ${nyClock.format(now)} NY` : ""}
          </Badge>
          {snapshot && (
            <Badge variant="secondary" title="LLM mode (set in output/paperbot_config.json)">
              LLM: {snapshot.config.llm_mode}
            </Badge>
          )}
          {paused && (
            <Badge variant="destructive" className="gap-1">
              <Pause /> Paused
            </Badge>
          )}
          {snapshot && (
            <span className="flex items-center gap-2 pl-1">
              {Object.entries(snapshot.streams).map(([name, status]) => (
                <StreamChip key={name} name={name} status={status} />
              ))}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label
            className={cn("flex items-center gap-2 text-sm", !live && "opacity-50")}
            title={offlineReason ?? (manual ? "Every new entry waits for your Approve" : "The bot enters on its own")}
          >
            <Switch
              checked={manual}
              disabled={!live || busy}
              onCheckedChange={(checked) => onCommand("set_approval", { mode: checked ? "manual" : "auto" })}
            />
            Ask before entering
          </label>
          <Button
            size="sm"
            variant="outline"
            disabled={!live || busy}
            title={offlineReason ?? (paused ? "Allow new entries again" : "Stop new entries; exits keep working")}
            onClick={() => onCommand(paused ? "resume" : "pause")}
          >
            {paused ? <Play data-icon="inline-start" /> : <Pause data-icon="inline-start" />}
            {paused ? "Resume" : "Pause"}
          </Button>
          <ConfirmButton
            variant="destructive"
            disabled={!live || busy || (snapshot?.positions.length ?? 0) === 0}
            title={offlineReason ?? "Close every position at market"}
            confirmLabel="Confirm flatten?"
            onConfirm={() => onCommand("flatten_all")}
          >
            <AlertTriangle data-icon="inline-start" />
            Flatten all
          </ConfirmButton>
        </div>
      </CardContent>
    </Card>
  );
}
