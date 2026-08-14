"use client";

import { useEffect, useRef } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import { Empty, EmptyDescription, EmptyTitle } from "@/components/ui/empty";
import { chartFont, chartTheme, toTime } from "@/components/charts/chart-theme";

export function EquityChart({
  dates,
  equity,
}: {
  dates: string[];
  equity: number[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: chartTheme.background },
        textColor: chartTheme.text,
        fontSize: 11,
        fontFamily: chartFont,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: chartTheme.grid },
        horzLines: { color: chartTheme.grid },
      },
      rightPriceScale: { borderColor: chartTheme.border },
      timeScale: { borderColor: chartTheme.border, rightOffset: 2 },
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: chartTheme.amber,
      lineWidth: 2,
      topColor: "rgba(217, 164, 65, 0.22)",
      bottomColor: "rgba(217, 164, 65, 0)",
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    series.setData(
      dates.map((date, i) => ({
        time: toTime(date) as UTCTimestamp,
        value: equity[i],
      })),
    );
    chartRef.current?.timeScale().fitContent();
  }, [dates, equity]);

  if (!dates.length) {
    return (
      <Empty>
        <EmptyTitle>No equity data yet</EmptyTitle>
        <EmptyDescription>
          Run <code className="text-foreground">python backtest.py</code> to generate the
          equity curve.
        </EmptyDescription>
      </Empty>
    );
  }

  return <div ref={containerRef} className="h-[260px] w-full" />;
}
