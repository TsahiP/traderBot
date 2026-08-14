"use client";

import { useEffect, useMemo } from "react";
import { Controller, useForm, type Path } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Play } from "lucide-react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/ui/spinner";
import {
  LabSchema,
  STRATEGY_DEFAULTS,
  type LabValues,
} from "@/lib/schemas";
import { useStrategies } from "@/hooks/use-api";

type LabInput = z.input<typeof LabSchema>;

interface LabFormProps {
  onRun: (values: LabValues) => void;
  loading: boolean;
  symbol: string;
}

export function LabForm({ onRun, loading, symbol }: LabFormProps) {
  const { data: strategies } = useStrategies();
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    control,
    formState: { errors },
  } = useForm<LabInput, unknown, LabValues>({
    resolver: zodResolver(LabSchema),
    defaultValues: {
      symbol,
      strategy: "sma_crossover",
      timeframe: "1d",
      start: "",
      end: "",
      qty: 10,
      capital: 100000,
      allow_short: false,
      cost_per_share: 0.01,
      fast: 10,
      slow: 50,
    },
  });

  const strategyId = watch("strategy");
  const timeframe = watch("timeframe");
  const spec = useMemo(
    () => strategies?.find((s) => s.id === strategyId),
    [strategies, strategyId],
  );

  useEffect(() => {
    if (!strategies) return;
    const current = strategyId;
    const params = STRATEGY_DEFAULTS[current]?.params ?? {};
    const timeframes = strategies.find((s) => s.id === current)?.timeframes;
    setValue("allow_short", STRATEGY_DEFAULTS[current]?.allow_short ?? false);
    if (timeframes && !timeframes.includes(timeframe)) {
      setValue("timeframe", STRATEGY_DEFAULTS[current]?.timeframe ?? "1d");
    }
    for (const [key, val] of Object.entries(params)) {
      setValue(key as Path<LabInput>, val);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, strategyId, setValue]);

  const err = (key: string) =>
    (errors as unknown as Record<string, { message?: string } | undefined>)[key];

  return (
    <form onSubmit={handleSubmit(onRun)} className="flex flex-col gap-5">
      <FieldGroup>
        <Field data-invalid={!!err("symbol")}>
          <FieldLabel htmlFor="symbol">Ticker</FieldLabel>
          <Input
            id="symbol"
            placeholder="SPY"
            aria-invalid={!!err("symbol")}
            autoComplete="off"
            spellCheck={false}
            className="font-mono uppercase"
            {...register("symbol")}
          />
          <FieldError errors={[err("symbol")]} />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field data-invalid={!!err("start")}>
            <FieldLabel htmlFor="start">From</FieldLabel>
            <Input
              id="start"
              type="date"
              aria-invalid={!!err("start")}
              className="tabular-nums"
              {...register("start")}
            />
            <FieldError errors={[err("start")]} />
          </Field>
          <Field data-invalid={!!err("end")}>
            <FieldLabel htmlFor="end">To</FieldLabel>
            <Input
              id="end"
              type="date"
              aria-invalid={!!err("end")}
              className="tabular-nums"
              {...register("end")}
            />
            <FieldError errors={[err("end")]} />
          </Field>
        </div>

        <Field data-invalid={!!err("strategy")}>
          <FieldLabel>Strategy</FieldLabel>
          <Controller
            control={control}
            name="strategy"
            render={({ field }) => (
              <Select
                value={field.value}
                onValueChange={(v) => field.onChange(v as string)}
              >
                <SelectTrigger className="w-full" size="default">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="w-full">
                  {(strategies ?? []).map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
          {spec && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {spec.description}
            </p>
          )}
          <FieldError errors={[err("strategy")]} />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field data-invalid={!!err("timeframe")}>
            <FieldLabel>Timeframe</FieldLabel>
            <Controller
              control={control}
              name="timeframe"
              render={({ field }) => (
                <Select
                  value={field.value}
                  onValueChange={(v) => field.onChange(v as string)}
                >
                  <SelectTrigger className="w-full" size="default">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="w-full">
                    {(spec?.timeframes ?? []).map((tf) => (
                      <SelectItem key={tf} value={tf}>
                        {tf}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
            <FieldError errors={[err("timeframe")]} />
          </Field>

          <Field data-invalid={!!err("cost_per_share")}>
            <FieldLabel htmlFor="cost_per_share">Cost / share ($)</FieldLabel>
            <Input
              id="cost_per_share"
              type="number"
              step="0.01"
              min={0}
              max={1}
              aria-invalid={!!err("cost_per_share")}
              className="tabular-nums"
              {...register("cost_per_share")}
            />
            <FieldError errors={[err("cost_per_share")]} />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {(spec?.params ?? []).map((p) => (
            <Field key={p.key} data-invalid={!!err(p.key)}>
              <FieldLabel htmlFor={p.key}>
                {p.label}
                {p.unit ? ` (${p.unit})` : ""}
              </FieldLabel>
              <Input
                id={p.key}
                type="number"
                step={p.step}
                min={p.min}
                max={p.max}
                aria-invalid={!!err(p.key)}
                className="tabular-nums"
                {...register(p.key as Path<LabInput>)}
              />
              <FieldError errors={[err(p.key)]} />
            </Field>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field data-invalid={!!err("qty")}>
            <FieldLabel htmlFor="qty">Shares per trade</FieldLabel>
            <Input
              id="qty"
              type="number"
              aria-invalid={!!err("qty")}
              className="tabular-nums"
              {...register("qty")}
            />
            <FieldError errors={[err("qty")]} />
          </Field>
          <Field data-invalid={!!err("capital")}>
            <FieldLabel htmlFor="capital">Capital ($)</FieldLabel>
            <Input
              id="capital"
              type="number"
              aria-invalid={!!err("capital")}
              className="tabular-nums"
              {...register("capital")}
            />
            <FieldError errors={[err("capital")]} />
          </Field>
        </div>

        <Field
          orientation="horizontal"
          data-invalid={!!err("allow_short")}
          className="items-center"
        >
          <Controller
            control={control}
            name="allow_short"
            render={({ field }) => (
              <Switch
                checked={field.value}
                onCheckedChange={(v) => field.onChange(v)}
              />
            )}
          />
          <FieldLabel>Allow shorts</FieldLabel>
          <FieldError errors={[err("allow_short")]} />
        </Field>
      </FieldGroup>

      <Button type="submit" disabled={loading} className="w-full">
        {loading ? (
          <Spinner data-icon="inline-start" />
        ) : (
          <Play data-icon="inline-start" className="fill-current" />
        )}
        {loading ? "Running…" : "Run backtest"}
      </Button>
    </form>
  );
}