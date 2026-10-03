"use client";

import { useState } from "react";
import { BellRing, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { saveSignalsConfig } from "@/hooks/use-signals";
import { cn } from "@/lib/utils";
import {
  TIMEFRAMES,
  type SignalPattern,
  type SignalsConfig,
  type WatchList,
} from "@/lib/schemas";

import { SymbolsField } from "./symbols-field";
import { slugId } from "./utils";

export function WatchlistFormCard({
  config,
  patterns,
  discordAvailable,
  onSaved,
}: {
  config: SignalsConfig;
  patterns: SignalPattern[];
  discordAvailable: string[];
  onSaved: () => void | Promise<unknown>;
}) {
  const [lists, setLists] = useState<WatchList[]>(config.lists);
  const [selectedId, setSelectedId] = useState(config.lists[0]?.id ?? "");
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);

  const selected = lists.find((l) => l.id === selectedId) ?? lists[0];

  const patchSelected = (patch: Partial<WatchList>) => {
    if (!selected) return;
    setLists((prev) => prev.map((l) => (l.id === selected.id ? { ...l, ...patch } : l)));
  };

  const addList = () => {
    const name = newName.trim();
    if (!name) return;
    if (lists.some((l) => l.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase())) {
      toast.error("Watchlist name already used");
      return;
    }
    const id = slugId(name, new Set(lists.map((l) => l.id)));
    const created: WatchList = {
      id,
      name,
      symbols: ["SPY"],
      telegram_timeframes: ["1d"],
      discord_timeframes: discordAvailable.includes("1d")
        ? ["1d"]
        : discordAvailable.length
          ? [discordAvailable[0]]
          : [],
      patterns: patterns.map((p) => p.id),
    };
    setLists((prev) => [...prev, created]);
    setSelectedId(id);
    setNewName("");
  };

  const removeSelected = () => {
    if (!selected || lists.length < 2) return;
    const next = lists.filter((l) => l.id !== selected.id);
    setLists(next);
    setSelectedId(next[0].id);
  };

  const onSave = async () => {
    const names = new Set<string>();
    const allowedDc = new Set(discordAvailable);
    for (const list of lists) {
      const key = list.name.trim().toLocaleLowerCase();
      if (!list.name.trim()) {
        toast.error("Every watchlist needs a name");
        return;
      }
      if (names.has(key)) {
        toast.error(`Duplicate watchlist name '${list.name.trim()}'`);
        return;
      }
      names.add(key);
      const badDc = list.discord_timeframes.filter((tf) => !allowedDc.has(tf));
      if (badDc.length) {
        toast.error(
          `'${list.name}': Discord webhook missing for ${badDc.join(", ")} — set .env or unselect`,
        );
        return;
      }
      if (!list.symbols.length || !list.telegram_timeframes.length || !list.patterns.length) {
        toast.error(`'${list.name}' needs symbols, Telegram timeframes, and patterns`);
        return;
      }
    }
    setSaving(true);
    try {
      await saveSignalsConfig({
        lists: lists.map(({ discord_route: _r, ...l }) => ({ ...l, name: l.name.trim() })),
      });
      await onSaved();
      toast.success("Saved — the bot picks it up on the next scheduler wake");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const bullish = patterns.filter((p) => p.direction === "long");
  const bearish = patterns.filter((p) => p.direction === "short");

  const togglePattern = (id: string) => {
    if (!selected) return;
    const on = new Set(selected.patterns);
    if (on.has(id)) on.delete(id);
    else on.add(id);
    patchSelected({ patterns: patterns.map((p) => p.id).filter((pid) => on.has(pid)) });
  };

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle className="text-sm">Signal watchlists</CardTitle>
        <CardDescription>
          Each list scans on its own schedule when a selected bar closes. Discord sends to the webhook for each timeframe (day / hour / minute / week).
        </CardDescription>
      </CardHeader>
      <CardContent>
        <FieldGroup>
          <Field>
            <FieldLabel>Watchlists</FieldLabel>
            <div className="flex flex-col gap-1">
              {lists.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  onClick={() => setSelectedId(l.id)}
                  className={cn(
                    "rounded-md px-2 py-1.5 text-left text-sm",
                    l.id === selected?.id ? "bg-muted font-medium" : "hover:bg-muted/60",
                  )}
                >
                  {l.name || "Untitled"}
                </button>
              ))}
            </div>
            <div className="flex gap-1.5">
              <Input
                aria-label="New watchlist name"
                placeholder="New list"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addList();
                  }
                }}
              />
              <Button type="button" variant="outline" size="icon" aria-label="Add watchlist" onClick={addList}>
                <Plus />
              </Button>
            </div>
          </Field>

          {selected && (
            <>
              <Field>
                <FieldLabel htmlFor="list-name">Name</FieldLabel>
                <div className="flex gap-1.5">
                  <Input
                    id="list-name"
                    value={selected.name}
                    onChange={(e) => patchSelected({ name: e.target.value })}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label="Delete watchlist"
                    disabled={lists.length < 2}
                    onClick={removeSelected}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </Field>

              <SymbolsField
                symbols={selected.symbols}
                setSymbols={(symbols) => patchSelected({ symbols })}
              />

              <Field>
                <FieldLabel>Telegram timeframes</FieldLabel>
                <ToggleGroup
                  multiple
                  value={selected.telegram_timeframes}
                  onValueChange={(v) =>
                    patchSelected({ telegram_timeframes: [...(v as string[])] })
                  }
                >
                  {TIMEFRAMES.map((tf) => (
                    <ToggleGroupItem key={tf} value={tf}>
                      {tf}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </Field>

              <Field>
                <FieldLabel>Discord timeframes</FieldLabel>
                {discordAvailable.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No Discord webhooks in <code className="font-mono">.env</code> — Telegram-only alerts.
                  </p>
                ) : (
                  <ToggleGroup
                    multiple
                    value={selected.discord_timeframes.filter((tf) => discordAvailable.includes(tf))}
                    onValueChange={(v) =>
                      patchSelected({ discord_timeframes: [...(v as string[])] })
                    }
                  >
                    {discordAvailable.map((tf) => (
                      <ToggleGroupItem key={`dc-${tf}`} value={tf}>
                        {tf}
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                )}
                <p className="text-xs text-muted-foreground">
                  1d→day, 1h→hour, 1m/5m/15m/30m→minute, 1w→week webhook
                </p>
              </Field>

              <Field>
                <FieldLabel>Bullish patterns</FieldLabel>
                <div className="flex flex-col gap-2">
                  {bullish.map((p) => (
                    <div key={p.id} className="flex items-center justify-between gap-3">
                      <span className="text-sm">{p.label}</span>
                      <Switch
                        size="sm"
                        checked={selected.patterns.includes(p.id)}
                        onCheckedChange={() => togglePattern(p.id)}
                      />
                    </div>
                  ))}
                </div>
              </Field>

              <Field>
                <FieldLabel>Bearish patterns</FieldLabel>
                <div className="flex flex-col gap-2">
                  {bearish.map((p) => (
                    <div key={p.id} className="flex items-center justify-between gap-3">
                      <span className="text-sm">{p.label}</span>
                      <Switch
                        size="sm"
                        checked={selected.patterns.includes(p.id)}
                        onCheckedChange={() => togglePattern(p.id)}
                      />
                    </div>
                  ))}
                </div>
              </Field>
            </>
          )}
        </FieldGroup>

        <Button type="button" onClick={onSave} disabled={saving} className="mt-5 w-full">
          {saving ? <Spinner data-icon="inline-start" /> : <BellRing data-icon="inline-start" />}
          {saving ? "Saving…" : "Save watchlists"}
        </Button>
      </CardContent>
    </Card>
  );
}
