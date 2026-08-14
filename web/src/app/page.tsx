"use client";

import { FlaskConical, LayoutDashboard } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TickerTape } from "@/components/ticker-tape";
import { StatCards } from "@/components/stat-cards";
import { EquityChart } from "@/components/charts/equity-chart";
import { TradeLedger } from "@/components/trade-ledger";
import { LabForm } from "@/components/lab-form";
import { LabChart } from "@/components/charts/lab-chart";
import { LabResults, RunError } from "@/components/lab-results";
import { Spinner } from "@/components/ui/spinner";
import { useLive, useStats, useTrades, useEquity } from "@/hooks/use-api";
import { useBacktestRun } from "@/hooks/use-backtest-run";

export default function Home() {
  const { data: live } = useLive();
  const { data: stats } = useStats();
  const { data: trades } = useTrades();
  const { data: equity } = useEquity();
  const { run, data: runData, error, isLoading, isValidating } = useBacktestRun();

  return (
    <div className="flex min-h-svh flex-col">
      <TickerTape live={live} />

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 pb-16 pt-6 sm:px-6">
        <Tabs defaultValue="dashboard" className="flex flex-col gap-4">
          <TabsList className="w-fit">
            <TabsTrigger value="dashboard" className="gap-1.5">
              <LayoutDashboard data-icon="inline-start" />
              Dashboard
            </TabsTrigger>
            <TabsTrigger value="lab" className="gap-1.5">
              <FlaskConical data-icon="inline-start" />
              Backtest lab
            </TabsTrigger>
          </TabsList>

          <TabsContent value="dashboard" className="flex flex-col gap-4">
            <StatCards stats={stats} live={live} />

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Equity curve</CardTitle>
                <CardDescription>
                  Backtest of {live?.symbol ?? "SPY"} · SMA crossover · from
                  output/equity_curve.csv
                </CardDescription>
              </CardHeader>
              <CardContent>
                <EquityChart dates={equity?.dates ?? []} equity={equity?.equity ?? []} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Trade ledger</CardTitle>
                <CardDescription>
                  Every closed round trip — paper bot trades tagged LIVE,
                  saved runs tagged BACKTEST
                </CardDescription>
              </CardHeader>
              <CardContent>
                <TradeLedger trades={trades?.trades} />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="lab" className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_1fr]">
              <Card className="h-fit">
                <CardHeader>
                  <CardTitle className="text-sm">Run a backtest</CardTitle>
                  <CardDescription>
                    Any ticker, any SMA pair — live chart with buy/sell markers
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <LabForm
                    onRun={run}
                    loading={isLoading || isValidating}
                    symbol={live?.symbol ?? "SPY"}
                  />
                </CardContent>
              </Card>

              <div className="flex min-w-0 flex-col gap-4">
                {error ? (
                  <RunError message={error.message} />
                ) : !runData ? (
                  <Card>
                    <CardContent className="py-24 text-center text-sm text-muted-foreground">
                      {isLoading ? (
                        <span className="inline-flex items-center gap-2">
                          <Spinner data-icon="inline-start" />
                          Running backtest…
                        </span>
                      ) : (
                        <>
                          Pick a strategy and hit <b className="text-foreground">Run</b> —
                          the chart draws candles, overlays and every buy/sell
                          the logic fires.
                        </>
                      )}
                    </CardContent>
                  </Card>
                ) : (
                  <>
                    <Card className="overflow-hidden">
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm">
                          {runData.meta.symbol} · {runData.meta.strategy_label} ·{" "}
                          {runData.meta.timeframe} · {runData.meta.qty} shares · $
                          {runData.meta.capital.toLocaleString("en-US")}
                          {runData.meta.cost_per_share > 0
                            ? ` · ${runData.meta.cost_per_share.toFixed(2)}/share costs`
                            : ""}
                        </CardTitle>
                        <CardDescription>
                          ▲ buy fill · ▼ sell fill · amber = forced session-end
                          close — orders execute at the next bar&apos;s open;
                          every marker has a row in Trade results below
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        <LabChart series={runData.series} markers={runData.markers} />
                      </CardContent>
                    </Card>

                    <Card className="overflow-hidden">
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm">Trade results</CardTitle>
                      </CardHeader>
                      <LabResults data={runData} />
                    </Card>
                  </>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </main>

      <footer className="border-t py-4 text-center text-xs text-muted-foreground">
        tradebot desk · dashboard.py API · paper trading only — never real money
        {isValidating && runData ? " · updating…" : ""}
      </footer>
    </div>
  );
}
