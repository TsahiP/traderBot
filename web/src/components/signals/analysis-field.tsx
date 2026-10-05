"use client";

import { Field, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { AnalysisChecks, SignalAnalysis } from "@/lib/schemas";

const MIN_SCORES = ["1", "2", "3", "4", "5"] as const;

const CHECKS: { id: keyof AnalysisChecks; label: string; detail: string }[] = [
  { id: "trend", label: "Trend", detail: "Close above EMA 200 for long, below it for short." },
  { id: "volume", label: "Volume", detail: "Signal bar at least 1.5× the prior 20 bars." },
  { id: "rsi", label: "RSI", detail: "Recent pullback: RSI under 40 for long, over 60 for short." },
  { id: "macd", label: "MACD", detail: "Histogram rising for long, falling for short." },
  { id: "support", label: "Support", detail: "Pattern touches EMA 50 or a Bollinger band." },
];

export function AnalysisField({
  analysis,
  onChange,
}: {
  analysis: SignalAnalysis;
  onChange: (analysis: SignalAnalysis) => void;
}) {
  const setCheck = (id: keyof AnalysisChecks, on: boolean) =>
    onChange({ ...analysis, checks: { ...analysis.checks, [id]: on } });

  return (
    <Field>
      <FieldLabel>Technical analysis</FieldLabel>
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm">Confluence score on alerts</span>
        <Switch
          size="sm"
          checked={analysis.enabled}
          onCheckedChange={(enabled) =>
            onChange({
              ...analysis,
              enabled,
              block: enabled ? analysis.block : false,
            })
          }
        />
      </div>
      {analysis.enabled && (
        <>
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm">Show details</span>
            <Switch
              size="sm"
              checked={analysis.show_details}
              onCheckedChange={(show_details) => onChange({ ...analysis, show_details })}
            />
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-xs text-muted-foreground">
              Required checks. A failed check blocks the alert on its own.
            </span>
            {CHECKS.map((check) => (
              <div key={check.id} className="flex flex-col gap-0.5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">{check.label}</span>
                  <Switch
                    size="sm"
                    checked={analysis.checks[check.id]}
                    onCheckedChange={(on) => setCheck(check.id, on)}
                  />
                </div>
                {analysis.show_details && (
                  <span className="text-xs text-muted-foreground">{check.detail}</span>
                )}
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm">Block weak signals</span>
            <Switch
              size="sm"
              checked={analysis.block}
              onCheckedChange={(block) => onChange({ ...analysis, block })}
            />
          </div>
          {analysis.block && (
            <div className="flex flex-col gap-1.5">
              <span className="text-xs text-muted-foreground">Minimum score</span>
              <ToggleGroup
                value={[String(analysis.min_score)]}
                onValueChange={(value) => {
                  const picked = value[value.length - 1];
                  if (!picked) return;
                  onChange({ ...analysis, min_score: Number(picked) });
                }}
              >
                {MIN_SCORES.map((score) => (
                  <ToggleGroupItem key={score} value={score}>
                    {score}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
          )}
        </>
      )}
    </Field>
  );
}
