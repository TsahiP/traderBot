"use client";

import { ConfirmButton } from "@/components/paper/confirm-button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { pnlClass } from "@/lib/format";
import { bracketProgress, px, signedR, signedUsd } from "@/lib/paper-format";
import type { PaperPosition } from "@/lib/schemas";
import { cn } from "@/lib/utils";

function BracketBar({ position }: { position: PaperPosition }) {
  const progress = bracketProgress(position);
  if (!progress) {
    return <div className="mt-2 text-xs text-muted-foreground">no stop/target on record</div>;
  }
  const winning = position.pnl >= 0;
  return (
    <div className="relative mt-3 h-1.5 rounded-full bg-muted" aria-label="price between stop and target">
      <div
        className={cn("absolute inset-y-0 left-0 rounded-full", winning ? "bg-up/50" : "bg-down/50")}
        style={{ width: `${progress.price * 100}%` }}
      />
      <div
        className="absolute -top-1 h-3.5 w-0.5 bg-foreground"
        style={{ left: `calc(${progress.entry * 100}% - 1px)` }}
        title="entry"
      />
    </div>
  );
}

function PositionRow({
  position,
  disabled,
  onClose,
}: {
  position: PaperPosition;
  disabled?: string;
  onClose: () => void;
}) {
  const t = position.trade;
  return (
    <div className="border-t py-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{position.symbol}</span>
          <Badge variant={position.side === "long" ? "default" : "destructive"}>
            {position.side} {position.qty}
          </Badge>
          {t?.adopted && <Badge variant="outline">ADOPTED</Badge>}
          <span className="text-xs text-muted-foreground">
            {t ? t.run_id : "untracked"} · {t?.flat_by ? `flat by ${t.flat_by}` : "GTC"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className={cn("font-mono text-sm tabular-nums", pnlClass(position.pnl))}>
            {signedUsd(position.pnl)}
            {position.r != null ? ` · ${signedR(position.r)}` : ""}
          </span>
          <ConfirmButton
            size="xs"
            disabled={Boolean(disabled)}
            title={disabled ?? `Close ${position.symbol} at market`}
            confirmLabel="Confirm close?"
            onConfirm={onClose}
          >
            Close
          </ConfirmButton>
        </div>
      </div>
      <BracketBar position={position} />
      <div className="mt-1.5 flex justify-between font-mono text-[11px] text-muted-foreground tabular-nums">
        <span>stop {px(t?.stop)}</span>
        <span>entry {px(position.avg_entry)}</span>
        <span className="text-foreground">last {px(position.price)}</span>
        <span>target {px(t?.target)}</span>
      </div>
      {t && (t.mae_r != null || t.mfe_r != null) && (
        <div className="mt-1 text-[11px] text-muted-foreground">
          worst {signedR(t.mae_r ?? 0)} · best {signedR(t.mfe_r ?? 0)}
        </div>
      )}
    </div>
  );
}

export function PaperPositions({
  positions,
  disabled,
  onClose,
}: {
  positions: PaperPosition[];
  disabled?: string;
  onClose: (symbol: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Open positions</CardTitle>
        <CardDescription>Live P&amp;L and where the price sits between the stop and the target</CardDescription>
      </CardHeader>
      <CardContent>
        {positions.length === 0 ? (
          <p className="text-sm text-muted-foreground">Flat - no open positions.</p>
        ) : (
          positions.map((p) => (
            <PositionRow key={p.symbol} position={p} disabled={disabled} onClose={() => onClose(p.symbol)} />
          ))
        )}
      </CardContent>
    </Card>
  );
}
