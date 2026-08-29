"use client";

import { useState } from "react";
import { AlertCircle, ArrowDown, ArrowUp, Bitcoin, Filter, RefreshCw, Shield, Sparkles } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCryptoOverview } from "@/hooks/use-api";
import { CryptoAsset } from "@/lib/schemas";

function getVolRegimeColor(regime: string): string {
  switch (regime) {
    case "Low Volatility":
      return "bg-emerald-500/10 text-emerald-500 border-emerald-500/30";
    case "Moderate Volatility":
      return "bg-blue-500/10 text-blue-500 border-blue-500/30";
    case "High Volatility":
      return "bg-amber-500/10 text-amber-500 border-amber-500/30";
    case "Extreme Volatility":
      return "bg-red-500/10 text-red-500 border-red-500/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

function getCorrelationBadge(corr: number) {
  if (corr >= 0.8) {
    return <Badge variant="outline" className="font-mono text-xs bg-blue-500/10 text-blue-500 border-blue-500/30">+{corr.toFixed(2)} (High)</Badge>;
  } else if (corr >= 0.4) {
    return <Badge variant="outline" className="font-mono text-xs bg-emerald-500/10 text-emerald-500 border-emerald-500/30">+{corr.toFixed(2)} (Mod)</Badge>;
  } else if (corr >= 0) {
    return <Badge variant="outline" className="font-mono text-xs bg-amber-500/10 text-amber-500 border-amber-500/30">+{corr.toFixed(2)} (Low)</Badge>;
  } else {
    return <Badge variant="outline" className="font-mono text-xs bg-purple-500/10 text-purple-500 border-purple-500/30">{corr.toFixed(2)} (Inv)</Badge>;
  }
}

function getSignalBadge(signal: string) {
  switch (signal) {
    case "STRONG BUY":
      return <Badge className="bg-emerald-600 font-bold text-xs">{signal}</Badge>;
    case "BUY":
      return <Badge className="bg-blue-600 font-bold text-xs">{signal}</Badge>;
    case "HOLD":
      return <Badge variant="secondary" className="font-medium text-xs">{signal}</Badge>;
    case "SELL":
    case "STRONG SELL":
      return <Badge variant="destructive" className="font-bold text-xs">{signal}</Badge>;
    default:
      return <Badge variant="outline" className="text-xs">{signal}</Badge>;
  }
}

export function CryptoMonitor() {
  const { data, error, isLoading, isValidating, mutate } = useCryptoOverview();
  const [selectedCategory, setSelectedCategory] = useState<string>("ALL");

  const assets = data?.assets || [];
  const categories = ["ALL", ...Array.from(new Set(assets.map((a) => a.category))).filter(Boolean)];

  const filteredAssets = selectedCategory === "ALL"
    ? assets
    : assets.filter((a) => a.category === selectedCategory);

  return (
    <div className="flex flex-col gap-6">
      {/* Category Pills & Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-card p-4 rounded-lg border">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground mr-1 flex items-center gap-1">
            <Filter className="h-3.5 w-3.5" /> Filter Category:
          </span>
          {categories.map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setSelectedCategory(cat)}
              className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
                selectedCategory === cat
                  ? "bg-primary text-primary-foreground border-primary font-semibold"
                  : "bg-muted/50 hover:bg-muted text-muted-foreground border-border"
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => mutate()}
            disabled={isLoading || isValidating}
            className="text-xs gap-1 h-8"
          >
            <RefreshCw className={`h-3 w-3 ${isValidating ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Loading State */}
      {isLoading && (
        <Card>
          <CardContent className="py-24 flex flex-col items-center justify-center gap-3">
            <Spinner className="h-8 w-8 text-primary" />
            <p className="text-sm text-muted-foreground">
              Scanning Top-20 Cryptocurrencies and calculating 30d BTC rolling correlations…
            </p>
          </CardContent>
        </Card>
      )}

      {/* Error State */}
      {error && !isLoading && (
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center text-sm text-destructive flex flex-col items-center gap-2">
            <AlertCircle className="h-6 w-6" />
            <p>Failed to load crypto market monitor: {error.message}</p>
            <Button variant="outline" size="sm" onClick={() => mutate()} className="mt-2">
              Try Again
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Data Table */}
      {data && !isLoading && (
        <Card className="overflow-hidden">
          <CardHeader className="pb-3 border-b bg-muted/20">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle className="text-base flex items-center gap-2">
                  <Bitcoin className="h-5 w-5 text-amber-500" />
                  Top-20 Cryptocurrency Intelligence Matrix
                </CardTitle>
                <CardDescription>
                  Real-time ranking, 30-day BTC correlation, volatility regime classification, and momentum score.
                </CardDescription>
              </div>
              <div className="text-xs text-muted-foreground font-mono">
                Updated: {data.timestamp}
              </div>
            </div>
          </CardHeader>

          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="text-xs">
                    <TableHead className="w-[180px]">Asset</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">24h Chg</TableHead>
                    <TableHead className="text-right">7d Chg</TableHead>
                    <TableHead className="text-right">30d Chg</TableHead>
                    <TableHead className="text-center">30d BTC Corr</TableHead>
                    <TableHead>Volatility Regime</TableHead>
                    <TableHead className="text-center">RSI (14)</TableHead>
                    <TableHead className="text-center">Score</TableHead>
                    <TableHead className="text-center">Signal</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredAssets.map((asset) => {
                    const is24hPos = asset.change_24h_pct >= 0;
                    const is7dPos = asset.change_7d_pct >= 0;
                    const is30dPos = asset.change_30d_pct >= 0;

                    return (
                      <TableRow key={asset.symbol} className="hover:bg-muted/40 transition-colors">
                        <TableCell className="font-medium">
                          <div>
                            <span className="font-bold font-mono text-foreground text-sm">
                              {asset.symbol.replace("-USD", "")}
                            </span>
                            <div className="text-[11px] text-muted-foreground">
                              {asset.name} · {asset.market_cap_class}
                            </div>
                          </div>
                        </TableCell>

                        <TableCell>
                          <span className="text-xs text-muted-foreground bg-muted/60 px-2 py-0.5 rounded">
                            {asset.category}
                          </span>
                        </TableCell>

                        <TableCell className="text-right font-mono font-bold text-sm">
                          ${asset.current_price < 1 ? asset.current_price.toFixed(4) : asset.current_price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </TableCell>

                        <TableCell className={`text-right font-mono text-xs ${is24hPos ? "text-emerald-500" : "text-red-500"}`}>
                          {is24hPos ? "+" : ""}{asset.change_24h_pct.toFixed(2)}%
                        </TableCell>

                        <TableCell className={`text-right font-mono text-xs ${is7dPos ? "text-emerald-500" : "text-red-500"}`}>
                          {is7dPos ? "+" : ""}{asset.change_7d_pct.toFixed(2)}%
                        </TableCell>

                        <TableCell className={`text-right font-mono text-xs ${is30dPos ? "text-emerald-500" : "text-red-500"}`}>
                          {is30dPos ? "+" : ""}{asset.change_30d_pct.toFixed(2)}%
                        </TableCell>

                        <TableCell className="text-center">
                          {getCorrelationBadge(asset.btc_correlation_30d)}
                        </TableCell>

                        <TableCell>
                          <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs border font-medium ${getVolRegimeColor(asset.volatility_regime)}`}>
                            {asset.volatility_regime.replace(" Volatility", "")} ({asset.realized_vol_30d_pct.toFixed(0)}%)
                          </div>
                        </TableCell>

                        <TableCell className="text-center font-mono text-xs">
                          <span
                            className={
                              asset.rsi_14 > 70
                                ? "text-red-500 font-bold"
                                : asset.rsi_14 < 30
                                ? "text-emerald-500 font-bold"
                                : "text-foreground"
                            }
                          >
                            {asset.rsi_14.toFixed(1)}
                          </span>
                        </TableCell>

                        <TableCell className="text-center font-mono font-bold text-xs">
                          {asset.composite_score}
                        </TableCell>

                        <TableCell className="text-center">
                          {getSignalBadge(asset.signal)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
