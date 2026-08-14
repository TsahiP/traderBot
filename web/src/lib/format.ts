const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const usd0 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

const num = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function fmtUsd(value: number): string {
  return usd.format(value);
}

export function fmtUsd0(value: number): string {
  return usd0.format(value);
}

export function fmtNum(value: number): string {
  return num.format(value);
}

export function fmtSignedUsd(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${usd.format(value)}`;
}

export function fmtPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
}

export function pnlClass(value: number): string {
  if (value > 0) return "text-up";
  if (value < 0) return "text-down";
  return "text-muted-foreground";
}
