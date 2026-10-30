"use client";

import { Check, CircleDashed, Clock, TriangleAlert, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useNow } from "@/hooks/use-now";
import { countdown, px } from "@/lib/paper-format";
import type { FunnelStage, PaperProposal } from "@/lib/schemas";
import { cn } from "@/lib/utils";

const STAGE_ICON = {
  pass: <Check />,
  fail: <X />,
  warn: <TriangleAlert />,
  skip: <CircleDashed />,
} as const;

function StageChip({ stage }: { stage: FunnelStage }) {
  return (
    <span
      title={stage.reason ?? undefined}
      className={cn(
        "inline-flex items-center gap-1 text-xs [&>svg]:size-3",
        stage.status === "pass" && "text-up",
        stage.status === "fail" && "text-down",
        stage.status === "warn" && "text-amber-600 dark:text-amber-400",
        stage.status === "skip" && "text-muted-foreground",
      )}
    >
      {STAGE_ICON[stage.status]}
      {stage.stage}
    </span>
  );
}

function ProposalCard({
  proposal,
  now,
  disabled,
  onApprove,
  onReject,
}: {
  proposal: PaperProposal;
  now: number | null;
  disabled?: string;
  onApprove: () => void;
  onReject: () => void;
}) {
  const plan = (proposal.plan ?? {}) as Record<string, unknown>;
  const meta = (plan.proposal ?? {}) as Record<string, unknown>;
  const intent = (plan.intent ?? {}) as Record<string, unknown>;
  const n = (k: string) => (typeof plan[k] === "number" ? (plan[k] as number) : null);
  const left = now != null ? countdown(typeof meta.expires_at === "string" ? meta.expires_at : null, now) : null;

  return (
    <div className="rounded-lg border-2 border-primary/40 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-semibold">
            Proposal #{proposal.id} · {proposal.symbol} {proposal.side}
          </div>
          <div className="text-xs text-muted-foreground">
            {proposal.run_id}
            {typeof intent.reason === "string" ? ` · ${intent.reason}` : ""}
          </div>
        </div>
        <Badge variant="secondary" className="gap-1 tabular-nums">
          <Clock /> {left ? `${left} left` : "expiring"}
        </Badge>
      </div>
      <div className="mt-2 font-mono text-xs text-muted-foreground">
        {proposal.side === "short" ? "Sell" : "Buy"} {proposal.qty ?? "?"} @ ~{px(n("entry"))} · stop {px(n("stop"))} ·
        target {px(n("target"))} ({n("target_r") ?? "?"}R) · max loss {proposal.max_loss != null ? `$${proposal.max_loss.toFixed(2)}` : "—"}
      </div>
      {proposal.stages && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
          {proposal.stages.map((s) => (
            <StageChip key={s.stage} stage={s} />
          ))}
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <Button size="sm" disabled={Boolean(disabled)} title={disabled} onClick={onApprove}>
          Approve
        </Button>
        <Button size="sm" variant="outline" disabled={Boolean(disabled)} title={disabled} onClick={onReject}>
          Reject
        </Button>
        <span className="self-center text-xs text-muted-foreground">re-checked at the current price before it is sent</span>
      </div>
    </div>
  );
}

export function PaperProposals({
  proposals,
  manual,
  disabled,
  onApprove,
  onReject,
}: {
  proposals: PaperProposal[];
  manual: boolean;
  disabled?: string;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
}) {
  const now = useNow();
  if (!manual && proposals.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Proposals</CardTitle>
        <CardDescription>
          {manual
            ? "Ask before entering is on - every new entry waits here for your decision"
            : "Ask before entering is off - these were proposed before it was switched off"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {proposals.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing waiting for approval.</p>
        ) : (
          proposals.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              now={now}
              disabled={disabled}
              onApprove={() => onApprove(p.id)}
              onReject={() => onReject(p.id)}
            />
          ))
        )}
      </CardContent>
    </Card>
  );
}
