import { cn } from "@/lib/utils";
import { fmtPct, fmtUsd } from "@/lib/format";
import type { RunMetrics } from "@/lib/schemas";

function Metric({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
        {label}
      </span>
      <b className="text-sm font-semibold tabular-nums text-foreground">{children}</b>
    </span>
  );
}

export function MetricsStrip({
  metrics,
  className,
}: {
  metrics: RunMetrics;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-5 gap-y-2 border-t px-4 py-3",
        className,
      )}
    >
      <Metric label="period">
        {metrics.start} → {metrics.end}
      </Metric>
      <Metric label="total">
        <span className={metrics.total_return >= 0 ? "text-up" : "text-down"}>
          {fmtPct(metrics.total_return)}
        </span>
      </Metric>
      <Metric label="buy&hold">{fmtPct(metrics.buy_hold)}</Metric>
      <Metric label="cagr">{fmtPct(metrics.cagr)}</Metric>
      <Metric label="max dd">
        <span className="text-down">{fmtPct(metrics.max_drawdown)}</span>
      </Metric>
      <Metric label="trades">{metrics.trades}</Metric>
      <Metric label="wins">
        {metrics.win_rate != null ? (
          <span className="text-up">{(metrics.win_rate * 100).toFixed(1)}%</span>
        ) : (
          "—"
        )}
      </Metric>
      <Metric label="costs">
        <span className="text-down">{fmtUsd(-metrics.costs_total)}</span>
      </Metric>
      <Metric label="final">{fmtUsd(metrics.final_equity)}</Metric>
    </div>
  );
}
