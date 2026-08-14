"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import { chartFont, chartTheme, toTime } from "@/components/charts/chart-theme";
import type { RunMarker, RunSeries } from "@/lib/schemas";

interface LabChartProps {
  series: RunSeries;
  markers: RunMarker[];
}

export function LabChart({ series, markers }: LabChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const fastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const slowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);

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
      timeScale: { borderColor: chartTheme.border, rightOffset: 4 },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: chartTheme.up,
      downColor: chartTheme.down,
      borderUpColor: chartTheme.up,
      borderDownColor: chartTheme.down,
      wickUpColor: chartTheme.up,
      wickDownColor: chartTheme.down,
    });
    const fast = chart.addSeries(LineSeries, {
      color: chartTheme.amber,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const slow = chart.addSeries(LineSeries, {
      color: chartTheme.slate,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    chartRef.current = chart;
    candleRef.current = candles;
    fastRef.current = fast;
    slowRef.current = slow;
    volumeRef.current = volume;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      fastRef.current = null;
      slowRef.current = null;
      volumeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const candles = candleRef.current;
    const fast = fastRef.current;
    const slow = slowRef.current;
    const volume = volumeRef.current;
    if (!candles || !fast || !slow || !volume) return;

    const times = series.dates.map((d) => toTime(d) as UTCTimestamp);

    candles.setData(
      series.dates.map((_, i) => ({
        time: times[i],
        open: series.open[i],
        high: series.high[i],
        low: series.low[i],
        close: series.close[i],
      })),
    );

    fast.setData(
      series.sma_fast.flatMap((value, i) =>
        value == null ? [] : [{ time: times[i], value }],
      ),
    );
    slow.setData(
      series.sma_slow.flatMap((value, i) =>
        value == null ? [] : [{ time: times[i], value }],
      ),
    );

    volume.setData(
      series.dates.map((_, i) => ({
        time: times[i],
        value: series.volume[i],
        color:
          series.close[i] >= series.open[i]
            ? "rgba(63, 191, 134, 0.28)"
            : "rgba(229, 83, 75, 0.28)",
      })),
    );

    createSeriesMarkers(
      candles,
      markers.map((m) => ({
        time: toTime(m.date) as UTCTimestamp,
        position: m.side === "sell" ? "aboveBar" : "belowBar",
        color: m.eod ? chartTheme.amber : m.side === "buy" ? chartTheme.up : chartTheme.down,
        shape: m.side === "sell" ? "arrowDown" : "arrowUp",
        text: m.eod ? "EOD" : m.side.toUpperCase(),
      })),
    );

    chartRef.current?.timeScale().fitContent();
  }, [series, markers]);

  return <div ref={containerRef} className="h-[420px] w-full" />;
}
