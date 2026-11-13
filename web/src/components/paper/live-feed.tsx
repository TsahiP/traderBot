"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { clock, describeEvent, type Tone } from "@/lib/paper-format";
import type { PaperEvent } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const TONE: Record<Tone, string> = {
  up: "text-up",
  down: "text-down",
  warn: "text-amber-600 dark:text-amber-400",
  info: "text-foreground",
  muted: "text-muted-foreground",
};

export function LiveFeed({ feed, live }: { feed: PaperEvent[]; live: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Live feed</CardTitle>
        <CardDescription>Every decision, order and fill as it happens</CardDescription>
      </CardHeader>
      <CardContent>
        {feed.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {live ? "Quiet so far." : "The feed starts when paperbot is online."}
          </p>
        ) : (
          <ol className="flex max-h-[28rem] flex-col overflow-y-auto font-mono text-xs">
            {feed.map((e) => {
              const { text, tone } = describeEvent(e);
              return (
                <li key={e.id ?? `${e.ts}-${e.type}`} className="flex gap-3 border-t py-1.5 first:border-t-0">
                  <span className="shrink-0 text-muted-foreground tabular-nums">{clock(e.ts)}</span>
                  <span className={cn("min-w-0 break-words", TONE[tone])}>{text}</span>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
