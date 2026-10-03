"use client";

import { useState } from "react";
import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

import { SYMBOL_RE } from "./utils";

export function SymbolsField({
  symbols,
  setSymbols,
}: {
  symbols: string[];
  setSymbols: (s: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addSymbol = () => {
    const s = draft.trim().toUpperCase();
    if (!s) return;
    if (!SYMBOL_RE.test(s)) {
      setError("Letters, digits, dots and dashes only (max 12 chars)");
      return;
    }
    if (!symbols.includes(s)) setSymbols([...symbols, s]);
    setDraft("");
    setError(null);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addSymbol();
    } else if (e.key === "Backspace" && !draft && symbols.length) {
      setSymbols(symbols.slice(0, -1));
    }
  };

  return (
    <Field data-invalid={!!error}>
      <FieldLabel htmlFor="signal-symbols">Symbols</FieldLabel>
      {symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {symbols.map((s) => (
            <Badge key={s} variant="secondary" className="gap-1 pr-1 font-mono">
              {s}
              <button
                type="button"
                aria-label={`Remove ${s}`}
                onClick={() => setSymbols(symbols.filter((x) => x !== s))}
                className="-mr-0.5 rounded-full p-0.5 hover:bg-muted-foreground/20"
              >
                <X data-icon="inline-start" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <Input
        id="signal-symbols"
        placeholder={symbols.length ? "Add another…" : "SPY"}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          if (error) setError(null);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => draft && addSymbol()}
        autoComplete="off"
        spellCheck={false}
        className="font-mono uppercase"
      />
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">Enter or comma to add a ticker</p>
      )}
    </Field>
  );
}
