"use client";

import { useState } from "react";

import { AnalysisResponse } from "@/lib/schemas";
import type { RunParams } from "@/hooks/use-backtest-run";

export function useLlmAnalysis(params: RunParams | null) {
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const analyze = async () => {
    if (!params || analyzing) return;
    setAnalyzing(true);
    setError(null);
    setAnalysis(null);
    try {
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error ?? "Analysis failed");
      setAnalysis(AnalysisResponse.parse(json));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  return { analysis, analyzing, error, analyze };
}