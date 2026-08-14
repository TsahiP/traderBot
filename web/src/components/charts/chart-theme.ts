export const chartTheme = {
  background: "#10151b",
  grid: "rgba(148, 163, 184, 0.08)",
  border: "rgba(148, 163, 184, 0.18)",
  text: "#8b97a5",
  up: "#3fbf86",
  down: "#e5534b",
  amber: "#d9a441",
  slate: "#7b8ca3",
} as const;

export const chartFont =
  '"Geist Mono", ui-monospace, "Cascadia Code", Consolas, monospace';

/**
 * Convert an API date string to a chart time (seconds).
 * Daily:    "YYYY-MM-DD"        -> UTC midnight
 * Intraday: "YYYY-MM-DD HH:MM"  -> wall-clock time (America/New_York
 *                                  timestamps sent without tz info)
 */
export function toTime(date: string): number {
  if (date.length <= 10) {
    return Date.parse(`${date}T00:00:00Z`) / 1000;
  }
  return Date.parse(`${date.replace(" ", "T")}:00`) / 1000;
}
