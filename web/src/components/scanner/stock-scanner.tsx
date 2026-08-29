"use client";

import { useState } from "react";
import { AlertCircle, ArrowUpRight, CheckCircle2, Flame, HelpCircle, RefreshCw, Search, ShieldAlert, Sparkles, TrendingUp } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useStockAnalysis } from "@/hooks/use-api";
import { StockAnalysisResponse } from "@/lib/schemas";

const QUICK_TICKERS = ["AAPL", "NVDA", "MSFT", "AMZN", "TSLA", "SPY", "QQQ", "GOOGL", "META"];

function getScoreColor(score: number | null): { text: string; bg: string; border: string } {
  if (score === null) return { text: "text-muted-foreground", bg: "bg-muted", border: "border-border" };
  if (score >= 75) return { text: "text-emerald-500", bg: "bg-emerald-500/10", border: "border-emerald-500/30" };
  if (score >= 60) return { text: "text-blue-500", bg: "bg-blue-500/10", border: "border-blue-500/30" };
  if (score >= 42) return { text: "text-amber-500", bg: "bg-amber-500/10", border: "border-amber-500/30" };
  if (score >= 28) return { text: "text-orange-500", bg: "bg-orange-500/10", border: "border-orange-500/30" };
  return { text: "text-red-500", bg: "bg-red-500/10", border: "border-red-500/30" };
}

function getSignalBadgeVariant(signal: string): "default" | "secondary" | "destructive" | "outline" {
  switch (signal) {
    case "STRONG BUY":
    case "BUY":
      return "default";
    case "HOLD":
      return "secondary";
    case "SELL":
    case "STRONG SELL":
      return "destructive";
    default:
      return "outline";
  }
}

export function StockScanner() {
  const [inputVal, setInputVal] = useState("AAPL");
  const [selectedTicker, setSelectedTicker] = useState("AAPL");

  const { data, error, isLoading, isValidating, mutate } = useStockAnalysis(selectedTicker);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputVal.trim()) {
      setSelectedTicker(inputVal.trim().toUpperCase());
    }
  };

  const selectQuick = (ticker: string) => {
    setInputVal(ticker);
    setSelectedTicker(ticker);
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Search Header */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <form onSubmit={handleSearch} className="flex flex-1 items-center gap-2 max-w-md">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={inputVal}
                  onChange={(e) => setInputVal(e.target.value)}
                  placeholder="Enter US ticker (e.g. AAPL, NVDA, SPY)..."
                  className="pl-9 uppercase"
                />
              </div>
              <Button type="submit" disabled={isLoading || isValidating}>
                {isLoading || isValidating ? <Spinner className="h-4 w-4" /> : "Analyze"}
              </Button>
            </form>

            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-muted-foreground mr-1">Quick:</span>
              {QUICK_TICKERS.map((sym) => (
                <button
                  key={sym}
                  type="button"
                  onClick={() => selectQuick(sym)}
                  className={`px-2.5 py-1 text-xs font-mono rounded-md border transition-colors ${
                    selectedTicker === sym
                      ? "bg-primary text-primary-foreground border-primary font-bold"
                      : "bg-muted/50 hover:bg-muted text-muted-foreground border-border"
                  }`}
                >
                  {sym}
                </button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Loading State */}
      {isLoading && (
        <Card>
          <CardContent className="py-20 flex flex-col items-center justify-center gap-3">
            <Spinner className="h-8 w-8 text-primary" />
            <p className="text-sm text-muted-foreground">
              Evaluating 8 institutional dimensions for <b className="text-foreground">{selectedTicker}</b>…
            </p>
          </CardContent>
        </Card>
      )}

      {/* Error State */}
      {error && !isLoading && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Analysis Error</AlertTitle>
          <AlertDescription>
            {error.message || `Failed to analyze ticker '${selectedTicker}'. Check if symbol is valid.`}
          </AlertDescription>
        </Alert>
      )}

      {/* Results View */}
      {data && !isLoading && (
        <div className="flex flex-col gap-6">
          {/* Executive Summary Card */}
          <Card className="overflow-hidden border-2">
            <div className="bg-muted/30 px-6 py-4 border-b flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div>
                  <h2 className="text-2xl font-bold tracking-tight">
                    {data.symbol}{" "}
                    <span className="text-sm font-normal text-muted-foreground">
                      · {data.company_name}
                    </span>
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    Latest Price: <b className="text-foreground font-mono">${data.current_price.toFixed(2)}</b> · As of {data.timestamp}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-4">
                <div className="text-right">
                  <div className="text-xs text-muted-foreground">Institutional Score</div>
                  <div className="text-2xl font-black font-mono flex items-center justify-end gap-1.5">
                    <span className={getScoreColor(data.overall_score).text}>{data.overall_score}</span>
                    <span className="text-xs text-muted-foreground font-normal">/ 100</span>
                  </div>
                </div>

                <Badge variant={getSignalBadgeVariant(data.recommendation)} className="text-sm px-3 py-1 font-bold">
                  {data.recommendation}
                </Badge>
              </div>
            </div>

            <CardContent className="pt-5">
              <div className="flex flex-wrap items-center justify-between text-xs text-muted-foreground border-b pb-4 mb-4 gap-4">
                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  <span>Model Confidence: <b>{data.confidence_pct}%</b></span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Flame className="h-4 w-4 text-blue-500" />
                  <span>8 Dimensions Evaluated (Dynamic Normalized Weights)</span>
                </div>
                <Button variant="ghost" size="sm" onClick={() => mutate()} className="h-7 text-xs gap-1">
                  <RefreshCw className="h-3 w-3" /> Refresh
                </Button>
              </div>

              {/* Risk Flags */}
              {data.risk_flags && data.risk_flags.length > 0 && (
                <div className="mb-6">
                  <Alert variant="destructive" className="bg-red-500/10 border-red-500/30 text-foreground">
                    <ShieldAlert className="h-4 w-4 text-red-500" />
                    <AlertTitle className="text-sm font-semibold text-red-500">
                      Risk Flags & Timing Warnings ({data.risk_flags.length})
                    </AlertTitle>
                    <AlertDescription>
                      <ul className="mt-2 list-disc list-inside space-y-1 text-xs">
                        {data.risk_flags.map((flag, idx) => (
                          <li key={idx} className="text-muted-foreground">
                            {flag}
                          </li>
                        ))}
                      </ul>
                    </AlertDescription>
                  </Alert>
                </div>
              )}

              {/* 8-Dimension Breakdown Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {data.dimensions.map((dim) => {
                  const colors = getScoreColor(dim.score);
                  return (
                    <div
                      key={dim.id}
                      className="flex flex-col justify-between p-4 rounded-lg border bg-card/60 hover:bg-card transition-colors"
                    >
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <div>
                          <div className="font-semibold text-sm flex items-center gap-1.5">
                            {dim.label}
                          </div>
                          <div className="text-[11px] text-muted-foreground">
                            Weight: {(dim.normalized_weight * 100).toFixed(0)}% (Base: {(dim.base_weight * 100).toFixed(0)}%)
                          </div>
                        </div>

                        <div className={`px-2.5 py-1 rounded-md text-xs font-mono font-bold border ${colors.bg} ${colors.text} ${colors.border}`}>
                          {dim.score !== null ? `${dim.score}` : "N/A"}
                        </div>
                      </div>

                      {/* Progress bar */}
                      <div className="w-full bg-muted h-1.5 rounded-full overflow-hidden my-2">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${
                            dim.score === null
                              ? "bg-transparent"
                              : dim.score >= 70
                              ? "bg-emerald-500"
                              : dim.score >= 50
                              ? "bg-blue-500"
                              : dim.score >= 35
                              ? "bg-amber-500"
                              : "bg-red-500"
                          }`}
                          style={{ width: `${dim.score ?? 0}%` }}
                        />
                      </div>

                      <p className="text-xs text-muted-foreground mt-1">
                        {dim.summary}
                      </p>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
