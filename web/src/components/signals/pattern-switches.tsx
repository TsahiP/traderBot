"use client";

import { Field, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import type { SignalPattern } from "@/lib/schemas";

function PatternGroup({
  label,
  patterns,
  selected,
  onToggle,
}: {
  label: string;
  patterns: SignalPattern[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <Field>
      <FieldLabel>{label}</FieldLabel>
      <div className="flex flex-col gap-2">
        {patterns.map((p) => (
          <div key={p.id} className="flex items-center justify-between gap-3">
            <span className="text-sm">{p.label}</span>
            <Switch
              size="sm"
              checked={selected.includes(p.id)}
              onCheckedChange={() => onToggle(p.id)}
            />
          </div>
        ))}
      </div>
    </Field>
  );
}

export function PatternSwitches({
  patterns,
  selected,
  onToggle,
}: {
  patterns: SignalPattern[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <>
      <PatternGroup
        label="Bullish patterns"
        patterns={patterns.filter((p) => p.direction === "long")}
        selected={selected}
        onToggle={onToggle}
      />
      <PatternGroup
        label="Bearish patterns"
        patterns={patterns.filter((p) => p.direction === "short")}
        selected={selected}
        onToggle={onToggle}
      />
    </>
  );
}
