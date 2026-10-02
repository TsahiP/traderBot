"use client";

import { useState } from "react";
import { BellRing, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  saveSignalsConfig,
  sendDiscordSignalTest,
  sendSignalTest,
  useSignalsConfig,
  useSignalsHistory,
  useSignalsStatus,
} from "@/hooks/use-signals";
import { cn } from "@/lib/utils";
import {
  TIMEFRAMES,
  type DiscordRoute,
  type SignalPattern,
  type SignalsConfig,
  type WatchList,
} from "@/lib/schemas";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const SYMBOL_RE = /^[A-Za-z0-9.\-]{1,12}$/;

function SymbolsField({ symbols, setSymbols }: {
  symbols: string[];
  setSymbols: (s: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addSymbol = () => {
    const s = draft.trim().toUpperCase();
    if (!s) return;
    if (!SYMBOL_RE.test(s)) {
      setError("Letters, digits, dots and dashes only (max 12 chars)");
      return;
    }
    if (!symbols.includes(s)) setSymbols([...symbols, s]);
    setDraft("");
    setError(null);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addSymbol();
    } else if (e.key === "Backspace" && !draft && symbols.length) {
      setSymbols(symbols.slice(0, -1));
    }
  };

  return (
    <Field data-invalid={!!error}>
      <FieldLabel htmlFor="signal-symbols">Symbols</FieldLabel>
      {symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {symbols.map((s) => (
            <Badge key={s} variant="secondary" className="gap-1 pr-1 font-mono">
              {s}
              <button
                type="button"
                aria-label={`Remove ${s}`}
                onClick={() => setSymbols(symbols.filter((x) => x !== s))}
                className="-mr-0.5 rounded-full p-0.5 hover:bg-muted-foreground/20"
              >
                <X data-icon="inline-start" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <Input
        id="signal-symbols"
        placeholder={symbols.length ? "Add another…" : "SPY"}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          if (error) setError(null);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => draft && addSymbol()}
        autoComplete="off"
        spellCheck={false}
        className="font-mono uppercase"
      />
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">Enter or comma to add a ticker</p>
      )}
    </Field>
  );
}

function slugId(name: string, used: Set<string>): string {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "list";
  base = base.slice(0, 32).replace(/-+$/g, "") || "list";
  let id = base;
  let n = 2;
  while (used.has(id)) {
    const suffix = `-${n}`;
    id = `${base.slice(0, 32 - suffix.length)}${suffix}`;
    n += 1;
  }
  return id;
}

function SignalsFormCard({ config, patterns, onSaved }: {
  config: SignalsConfig;
  patterns: SignalPattern[];
  onSaved: () => void | Promise<unknown>;
}) {
  const [lists, setLists] = useState<WatchList[]>(config.lists);
  const [selectedId, setSelectedId] = useState(config.lists[0]?.id ?? "");
  const [pollMinutes, setPollMinutes] = useState(config.poll_minutes);
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
      timeframes: ["1d"],
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
    if (pollMinutes < 1 || pollMinutes > 60) {
      toast.error("Poll interval must be 1-60 minutes");
      return;
    }
    const names = new Set<string>();
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
      if (!list.symbols.length || !list.timeframes.length || !list.patterns.length) {
        toast.error(`'${list.name}' needs a symbol, timeframe and pattern`);
        return;
      }
    }
    setSaving(true);
    try {
      await saveSignalsConfig({
        poll_minutes: pollMinutes,
        lists: lists.map((l) => ({ ...l, name: l.name.trim() })),
      });
      await onSaved();
      toast.success("Saved - the bot picks it up on its next cycle");
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
          Each list has its own tickers, timeframes and patterns. Alerts go to Telegram; optionally mirror to a Discord channel per list.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <FieldGroup>
          <Field data-invalid={pollMinutes < 1 || pollMinutes > 60}>
            <FieldLabel htmlFor="signal-poll">Poll interval (minutes)</FieldLabel>
            <Input
              id="signal-poll"
              type="number"
              min={1}
              max={60}
              value={pollMinutes}
              onChange={(e) => setPollMinutes(Number(e.target.value))}
              className="tabular-nums"
            />
          </Field>

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
                <FieldLabel>Discord channel</FieldLabel>
                <Select
                  value={selected.discord_route ?? "none"}
                  onValueChange={(v) =>
                    patchSelected({
                      discord_route: v === "none" ? null : (v as DiscordRoute),
                    })
                  }
                >
                  <SelectTrigger className="w-full" size="default">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="w-full">
                    <SelectItem value="none">None</SelectItem>
                    <SelectItem value="day">Day trade</SelectItem>
                    <SelectItem value="hour">Hour trade</SelectItem>
                    <SelectItem value="week">Week trade</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Uses the matching webhook from <code className="font-mono">.env</code>
                </p>
              </Field>

              <Field>
                <FieldLabel>Timeframes</FieldLabel>
                <ToggleGroup
                  multiple
                  value={selected.timeframes}
                  onValueChange={(v) => patchSelected({ timeframes: [...(v as string[])] })}
                >
                  {TIMEFRAMES.map((tf) => (
                    <ToggleGroupItem key={tf} value={tf}>
                      {tf}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
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

const DISCORD_TEST_ROUTES: { route: DiscordRoute; label: string }[] = [
  { route: "day", label: "Day trade" },
  { route: "hour", label: "Hour trade" },
  { route: "week", label: "Week trade" },
];

export function SignalsPanel() {
  const { data: cfgData, mutate: mutateConfig } = useSignalsConfig();
  const { data: status } = useSignalsStatus();
  const { data: history } = useSignalsHistory(50);
  const [testing, setTesting] = useState(false);
  const [discordTesting, setDiscordTesting] = useState<DiscordRoute | null>(null);

  const onTest = async () => {
    setTesting(true);
    try {
      await sendSignalTest();
      toast.success("Test message sent - check Telegram");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };

  const onDiscordTest = async (route: DiscordRoute) => {
    setDiscordTesting(route);
    try {
      await sendDiscordSignalTest(route);
      toast.success(`Test sent - check Discord (${route})`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed");
    } finally {
      setDiscordTesting(null);
    }
  };

  const signals = history?.signals ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
      {cfgData ? (
        <SignalsFormCard
          key={JSON.stringify(cfgData.config)}
          config={cfgData.config}
          patterns={cfgData.patterns}
          onSaved={() => mutateConfig()}
        />
      ) : (
        <Card className="h-fit">
          <CardContent className="flex flex-col gap-3 py-6">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      )}

      <div className="flex min-w-0 flex-col gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Signal bot</CardTitle>
            <CardDescription>
              Listens for completed candles and pushes pattern alerts (Telegram + optional Discord per watchlist)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-2">
            <Badge variant={status?.running ? "default" : "secondary"}>
              {status?.running ? (
                <span className="size-1.5 rounded-full bg-primary-foreground" />
              ) : null}
              {status?.running ? "Listening" : "Not running"}
            </Badge>
            {status?.last_check && (
              <span className="text-xs text-muted-foreground">
                last check{" "}
                {new Date(status.last_check).toLocaleString()}
                {typeof status.heartbeat_age_s === "number"
                  ? ` (${Math.round(status.heartbeat_age_s)}s ago)`
                  : ""}
              </span>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Telegram</CardTitle>
            <CardDescription>All watchlists share the same bot token and chat id</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-2">
            {cfgData && (
              <Badge variant={cfgData.telegram_configured ? "secondary" : "destructive"}>
                {cfgData.telegram_configured ? "Configured" : "Missing TELEGRAM_* in .env"}
              </Badge>
            )}
            <div className="ml-auto">
              <Button type="button" variant="outline" size="sm" onClick={onTest} disabled={testing}>
                {testing ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <BellRing data-icon="inline-start" />
                )}
                {testing ? "Sending…" : "Send test"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Discord webhooks</CardTitle>
            <CardDescription>Day / hour / week channels — set per watchlist above</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {DISCORD_TEST_ROUTES.map(({ route, label }) => {
                const ok = cfgData?.discord_configured?.[route];
                return (
                  <Badge key={route} variant={ok ? "secondary" : "destructive"}>
                    {label} {ok ? "configured" : "missing webhook"}
                  </Badge>
                );
              })}
            </div>
            <div className="flex flex-wrap gap-2">
              {DISCORD_TEST_ROUTES.map(({ route, label }) => {
                const configured = cfgData?.discord_configured?.[route];
                const busy = discordTesting === route;
                return (
                  <Button
                    key={route}
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!configured || busy || discordTesting !== null}
                    onClick={() => onDiscordTest(route)}
                  >
                    {busy ? <Spinner data-icon="inline-start" /> : null}
                    {busy ? "Sending…" : `Test ${label.toLowerCase()}`}
                  </Button>
                );
              })}
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Recent signals</CardTitle>
            <CardDescription>Every alert the bot has sent - newest first</CardDescription>
          </CardHeader>
          <CardContent>
            {signals.length === 0 ? (
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <BellRing />
                  </EmptyMedia>
                  <EmptyTitle>No signals yet</EmptyTitle>
                  <EmptyDescription>
                    When a watched pattern completes on the last closed bar, the
                    bot sends an alert to Telegram and it shows up here. Run{" "}
                    <code className="font-mono">signalbot.py</code> in its own
                    terminal to start listening.
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>List</TableHead>
                    <TableHead>Symbol</TableHead>
                    <TableHead>TF</TableHead>
                    <TableHead>Pattern</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className="text-right">Close</TableHead>
                    <TableHead className="text-right">Entry</TableHead>
                    <TableHead className="text-right">Stop</TableHead>
                    <TableHead className="text-right">Target</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {signals.map((s) => (
                    <TableRow key={s.key}>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {new Date(s.ts).toLocaleString()}
                      </TableCell>
                      <TableCell>{s.list ?? "—"}</TableCell>
                      <TableCell className="font-mono font-medium">{s.symbol}</TableCell>
                      <TableCell>{s.timeframe}</TableCell>
                      <TableCell>{s.label}</TableCell>
                      <TableCell className={s.direction === "long" ? "text-up" : "text-down"}>
                        {s.direction === "long" ? "LONG" : "SHORT"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        ${s.close.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        ${s.entry.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {s.stop != null ? `$${s.stop.toFixed(2)}` : "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {s.target != null ? `$${s.target.toFixed(2)}` : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
