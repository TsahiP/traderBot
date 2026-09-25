"use client";

import { useState } from "react";
import { BellRing, X } from "lucide-react";
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
  sendSignalTest,
  useSignalsConfig,
  useSignalsHistory,
  useSignalsStatus,
} from "@/hooks/use-signals";
import { TIMEFRAMES, type SignalPattern, type SignalsConfig } from "@/lib/schemas";

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

function SignalsFormCard({ config, patterns, onSaved }: {
  config: SignalsConfig;
  patterns: SignalPattern[];
  onSaved: () => void | Promise<unknown>;
}) {
  const [symbols, setSymbols] = useState(config.symbols);
  const [timeframes, setTimeframes] = useState<string[]>(config.timeframes);
  const [patternsOn, setPatternsOn] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(patterns.map((p) => [p.id, config.patterns.includes(p.id)])),
  );
  const [pollMinutes, setPollMinutes] = useState(config.poll_minutes);
  const [saving, setSaving] = useState(false);

  const togglePattern = (id: string) =>
    setPatternsOn((p) => ({ ...p, [id]: !p[id] }));

  const onSave = async () => {
    if (!symbols.length || !timeframes.length || !Object.values(patternsOn).some(Boolean)) {
      toast.error("Pick at least one symbol, timeframe and pattern");
      return;
    }
    setSaving(true);
    try {
      await saveSignalsConfig({
        symbols,
        timeframes,
        patterns: Object.keys(patternsOn).filter((k) => patternsOn[k]),
        poll_minutes: pollMinutes,
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

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle className="text-sm">Signal watchlist</CardTitle>
        <CardDescription>
          Which tickers, timeframes and patterns the bot listens for - alerts
          land in Telegram with a chart image
        </CardDescription>
      </CardHeader>
      <CardContent>
        <FieldGroup>
          <SymbolsField symbols={symbols} setSymbols={setSymbols} />

          <Field>
            <FieldLabel>Timeframes</FieldLabel>
            <ToggleGroup
              multiple
              value={timeframes}
              onValueChange={(v) => setTimeframes([...(v as string[])])}
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
                    checked={!!patternsOn[p.id]}
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
                    checked={!!patternsOn[p.id]}
                    onCheckedChange={() => togglePattern(p.id)}
                  />
                </div>
              ))}
            </div>
          </Field>

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
        </FieldGroup>

        <Button type="button" onClick={onSave} disabled={saving} className="mt-5 w-full">
          {saving ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <BellRing data-icon="inline-start" />
          )}
          {saving ? "Saving…" : "Save watchlist"}
        </Button>
      </CardContent>
    </Card>
  );
}

export function SignalsPanel() {
  const { data: cfgData, mutate: mutateConfig } = useSignalsConfig();
  const { data: status } = useSignalsStatus();
  const { data: history } = useSignalsHistory(50);
  const [testing, setTesting] = useState(false);

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

  const signals = history?.signals ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_1fr]">
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
              Listens for completed candles and pushes pattern alerts to Telegram
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-2">
            <Badge variant={status?.running ? "default" : "secondary"}>
              {status?.running ? (
                <span className="size-1.5 rounded-full bg-primary-foreground" />
              ) : null}
              {status?.running ? "Listening" : "Not running"}
            </Badge>
            {cfgData && (
              <Badge variant={cfgData.telegram_configured ? "secondary" : "destructive"}>
                Telegram {cfgData.telegram_configured ? "configured" : "missing keys"}
              </Badge>
            )}
            {status?.last_check && (
              <span className="text-xs text-muted-foreground">
                last check{" "}
                {new Date(status.last_check).toLocaleString()}
                {typeof status.heartbeat_age_s === "number"
                  ? ` (${Math.round(status.heartbeat_age_s)}s ago)`
                  : ""}
              </span>
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
