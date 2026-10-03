import type { DiscordRoute } from "@/lib/schemas";

export const DISCORD_TEST_ROUTES: { route: DiscordRoute; label: string }[] = [
  { route: "day", label: "Day trade" },
  { route: "hour", label: "Hour trade" },
  { route: "minute", label: "Minute trade" },
  { route: "week", label: "Week trade" },
];
