"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Radio, Newspaper, ExternalLink } from "lucide-react";

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
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { fetchFundiRefresh, useFundi, useFundiNews } from "@/hooks/use-fundi";
import type { FundaSnapshot, FundaField, NewsItem } from "@/lib/schemas";

const SRC_LABEL: Record<string, string> = {
  sec_edgar: "SEC",
  fmp: "FMP",
  finnhub: "FH",
  yfinance: "YF",
  twelvedata: "TD",
  alphavantage: "AV",
  coingecko: "CG",
};

const CAP_FIELDS = new Set([
  "mcap",
  "revenue_ttm",
  "fcf_ttm",
  "avg_volume",
  "volume_24h",
  "supply_circulating",
  "supply_total",
  "shares_out",
]);

const PCT_FIELDS = new Set([
  "revenue_growth_yoy",
  "gross_margin",
  "operating_margin",
  "net_margin",
  "roe",
  "div_yield",
  "change_24h_pct",
  "change_7d_pct",
  "change_30d_pct",
  "ath_change_pct",
]);

const TWO_DP_FIELDS = new Set([
  "pe",
  "forward_pe",
  "peg",
  "de_ratio",
  "current_ratio",
  "beta",
  "price",
  "week52_low",
  "week52_high",
  "ath",
  "rank",
]);

const EPS_FIELDS = new Set(["eps_ttm"]);

function abbrev(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(0);
}

function fmtValue(name: string, value: number | string): string {
  if (typeof value === "string") return value;
  if (CAP_FIELDS.has(name)) return abbrev(value);
  if (PCT_FIELDS.has(name)) return `${value.toFixed(1)}%`;
  if (EPS_FIELDS.has(name)) return value.toFixed(2);
  if (TWO_DP_FIELDS.has(name)) return value.toFixed(2);
  return value.toLocaleString("en-US");
}

function FieldRow({ name, field }: { name: string; field: FundaField | undefined }) {
  if (!field) return null;
  const label = name.replace(/_ttm$/, " (TTM)").replace(/_/g, " ");
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-xs capitalize text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1 text-sm tabular-nums">
        {fmtValue(name, field.v)}
        <Tooltip>
          <TooltipTrigger className="inline-flex">
            <Badge variant="outline" className="px-1 py-0 text-[9px] leading-3">
              {SRC_LABEL[field.src] ?? field.src}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>{`source ${field.src} · fetched ${field.ts.slice(0, 16)}`}</TooltipContent>
        </Tooltip>
      </span>
    </div>
  );
}

function SentimentDot({ sentiment }: { sentiment: number | null }) {
  if (sentiment == null) return null;
  const [glyph, cls] =
    sentiment > 0.05
      ? ["↑", "text-up"]
      : sentiment < -0.05
        ? ["↓", "text-down"]
        : ["→", "text-muted-foreground"];
  return (
    <span className={`text-xs ${cls}`} title={`sentiment ${sentiment.toFixed(2)}`}>
      {glyph} {sentiment.toFixed(2)}
    </span>
  );
}

function NewsRow({ item }: { item: NewsItem }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-border/60 py-2 last:border-0">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="font-mono font-medium text-foreground">{item.symbol}</span>
        <span>{new Date(item.ts).toLocaleString()}</span>
        <span className="ml-auto">{item.source ?? ""}</span>
        <SentimentDot sentiment={item.sentiment} />
      </div>
      <a
        href={item.url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm font-medium leading-snug hover:underline"
      >
        {item.title}
        <ExternalLink className="mb-0.5 ml-1 inline h-3 w-3 text-muted-foreground" />
      </a>
      {item.summary ? (
        <p className="line-clamp-2 text-xs text-muted-foreground">{item.summary}</p>
      ) : null}
    </div>
  );
}

const STOCK_ROW_KEYS = [
  "price",
  "mcap",
  "pe",
  "eps_ttm",
  "revenue_ttm",
  "revenue_growth_yoy",
  "gross_margin",
  "net_margin",
  "roe",
  "fcf_ttm",
  "de_ratio",
  "div_yield",
  "beta",
  "shares_out",
  "avg_volume",
  "next_earnings",
];

const CRYPTO_ROW_KEYS = [
  "price",
  "mcap",
  "rank",
  "volume_24h",
  "change_24h_pct",
  "change_7d_pct",
  "change_30d_pct",
  "ath_change_pct",
  "supply_circulating",
];

function SymbolCard({
  symbol,
  snap,
  onRefresh,
  refreshing,
}: {
  symbol: string;
  snap: FundaSnapshot;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const fields = snap.fields;
  const rowKeys = snap.asset === "crypto" ? CRYPTO_ROW_KEYS : STOCK_ROW_KEYS;
  return (
    <Card className="flex flex-col gap-3">
      <CardHeader className="pb-0">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="font-mono text-base">{snap.symbol}</CardTitle>
            <CardDescription className="line-clamp-1">
              {(fields["name"]?.v as string) ?? symbol}
              {fields["sector"] ? ` · ${String(fields["sector"].v)}` : ""}
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onRefresh}
            disabled={refreshing}
            title={refreshing ? "Refreshing…" : `Refresh ${snap.symbol}`}
          >
            {refreshing ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          {rowKeys.map((k) => (
            <FieldRow key={k} name={k} field={fields[k]} />
          ))}
        </div>

        {snap.asset === "stock" && snap.quarters.length > 0 ? (
          <div className="rounded-md border border-border/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="h-7 text-xs">Q end</TableHead>
                  <TableHead className="h-7 text-right text-xs">Revenue</TableHead>
                  <TableHead className="h-7 text-right text-xs">Net inc.</TableHead>
                  <TableHead className="h-7 text-right text-xs">EPS</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...snap.quarters].reverse().map((q) => (
                  <TableRow key={q.end}>
                    <TableCell className="py-1 text-xs font-mono">{q.end}</TableCell>
                    <TableCell className="py-1 text-right text-xs tabular-nums">
                      {q.revenue != null ? abbrev(q.revenue) : "—"}
                    </TableCell>
                    <TableCell className="py-1 text-right text-xs tabular-nums">
                      {q.net_income != null ? abbrev(q.net_income) : "—"}
                    </TableCell>
                    <TableCell className="py-1 text-right text-xs tabular-nums">
                      {q.eps != null ? q.eps.toFixed(2) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : null}

        <div className="mt-auto flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
          {snap.sources_used.map((src) => (
            <Badge key={src} variant="secondary" className="px-1.5 py-0 text-[9px]">
              {SRC_LABEL[src] ?? src}
            </Badge>
          ))}
          <span className="ml-auto">{snap.updated.slice(0, 16).replace("T", " ")}</span>
        </div>
      </CardContent>
    </Card>
  );
}

export function FundiPanel() {
  const { data, error, isLoading, mutate } = useFundi();
  const { data: newsData } = useFundiNews();
  const [listId, setListId] = useState<string>("");
  const [refreshing, setRefreshing] = useState<string[]>([]);

  const lists = useMemo(() => data?.lists ?? [], [data]);
  const selected = useMemo(() => {
    const fallback = lists.find((l) => l.id === listId) ?? lists[0];
    return fallback ?? null;
  }, [lists, listId]);

  const symbols = selected
    ? [...selected.stocks, ...selected.crypto]
    : Object.keys(data?.snapshots ?? {});

  const doRefresh = (symbol: string) => {
    if (refreshing.length > 0) return; // one refresh pass at a time (API quota)
    setRefreshing([symbol]);
    fetchFundiRefresh(symbol)
      .then((res) => {
        toast(`Refreshed: ${res.refreshed.join(", ")}`);
        return res;
      })
      .then(() => mutate())
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setRefreshing([]));
  };
  const refreshingAll = refreshing.includes("ALL");

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-sm">Watchlist</CardTitle>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span
                className={`inline-flex items-center gap-1 ${data?.bot.running ? "text-up" : "text-down"}`}
              >
                <Radio className="h-3 w-3" />
                {data?.bot.running ? "fundabot running" : "fundabot off — manual refresh works"}
              </span>
              {data
                ? Object.entries(data.quota)
                    .filter(([, q]) => q.cap != null)
                    .map(([name, q]) => (
                      <Badge
                        key={name}
                        variant={q.ok ? "secondary" : "destructive"}
                        className="text-[10px]"
                      >
                        {SRC_LABEL[name] ?? name} {q.used}/{q.cap}
                      </Badge>
                    ))
                : null}
              <Button
                variant="outline"
                size="sm"
                onClick={() => doRefresh("ALL")}
                disabled={refreshingAll || refreshing.length > 0 || !data}
              >
                {refreshingAll ? (
                  <>
                    <Spinner data-icon="inline-start" /> Refreshing…
                  </>
                ) : (
                  "Refresh stocks"
                )}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {lists.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {lists.map((l) => (
                <Button
                  key={l.id}
                  size="sm"
                  variant={selected?.id === l.id ? "default" : "outline"}
                  onClick={() => setListId(l.id)}
                >
                  {l.name} ({l.stocks.length + l.crypto.length})
                </Button>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Add symbols in the Signals tab — watchlists feed this view.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_380px]">
        <div className="flex flex-col gap-4">
          {error ? (
            <Card>
              <CardContent className="py-16 text-center text-sm text-muted-foreground">
                {error.message}
              </CardContent>
            </Card>
          ) : isLoading ? (
            <Card>
              <CardContent className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground">
                <Spinner data-icon="inline-start" /> Loading fundamentals…
              </CardContent>
            </Card>
          ) : symbols.length === 0 ? (
            <Card>
              <CardContent className="py-6 text-center text-sm text-muted-foreground">
                This watchlist has no symbols yet.
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {symbols.map((symbol) => {
                const snap = data?.snapshots[symbol];
                return snap ? (
                  <SymbolCard
                    key={symbol}
                    symbol={symbol}
                    snap={snap}
                    onRefresh={() => doRefresh(symbol)}
                    refreshing={refreshing.includes(symbol)}
                  />
                ) : (
                  <SymbolCard
                    key={symbol}
                    symbol={symbol}
                    snap={{
                      symbol,
                      asset: symbol.toUpperCase().endsWith("-USD") ? "crypto" : "stock",
                      updated: "",
                      fields: {},
                      quarters: [],
                      sources_used: [],
                      errors: [],
                    }}
                    onRefresh={() => doRefresh(symbol)}
                    refreshing={refreshing.includes(symbol)}
                  />
                );
              })}
            </div>
          )}
        </div>

        <Card className="h-fit">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">News feed</CardTitle>
            <CardDescription>
              Newest stories across the watchlist — fundabot pushes to the
              stock-news and crypto-news Discord channels
            </CardDescription>
          </CardHeader>
          <CardContent>
            {(newsData?.news ?? []).length === 0 ? (
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <Newspaper />
                  </EmptyMedia>
                  <EmptyTitle>No news cached yet</EmptyTitle>
                  <EmptyDescription>
                    Run <code className="font-mono">fundabot.py</code> and add the
                    FMP/Finnhub keys to <code className="font-mono">.env</code> for
                    wider coverage — Google News fallback works without keys.
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <div className="flex flex-col">
                {(newsData?.news ?? []).slice(0, 30).map((item) => (
                  <NewsRow key={item.id} item={item} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
