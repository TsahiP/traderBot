"use client";

import { Brain, TriangleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useLlmAnalysis } from "@/hooks/use-llm-analysis";
import type { RunParams } from "@/hooks/use-backtest-run";

function AnalysisText({ text }: { text: string }) {
  return (
    <div className="space-y-1.5 text-sm leading-relaxed">
      {text.split("\n").map((line, i) => {
        if (line.startsWith("### ")) {
          return (
            <h4 key={i} className="pt-2 font-semibold text-foreground first:pt-0">
              {line.slice(4)}
            </h4>
          );
        }
        if (line.startsWith("## ")) {
          return (
            <h3 key={i} className="pt-2 font-semibold text-foreground first:pt-0">
              {line.slice(3)}
            </h3>
          );
        }
        if (line.startsWith("- ") || line.startsWith("* ")) {
          return (
            <p key={i} className="pl-3 text-foreground/80">
              • {line.slice(2)}
            </p>
          );
        }
        if (line.trim() === "") return null;
        return (
          <p key={i} className="text-foreground/80">
            {line}
          </p>
        );
      })}
    </div>
  );
}

export function LlmAnalysis({ params }: { params: RunParams | null }) {
  const { analysis, analyzing, error, analyze } = useLlmAnalysis(params);

  return (
    <div className="flex flex-col gap-3 border-t px-4 py-3">
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={analyze}
          disabled={!params || analyzing}
        >
          {analyzing ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <Brain data-icon="inline-start" />
          )}
          {analyzing ? "Local model is thinking…" : "Analyze with local LLM"}
        </Button>
        {!params && (
          <span className="text-xs text-muted-foreground">
            Run a backtest first
          </span>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <TriangleAlert data-icon="inline-start" />
          <AlertTitle>Analysis failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {analysis && (
        <div className="rounded-md border bg-muted/40 p-3">
          <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {analysis.model} · local analysis, not financial advice
          </p>
          <AnalysisText text={analysis.analysis} />
        </div>
      )}
    </div>
  );
}