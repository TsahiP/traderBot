import type { DiscordRoute } from "@/lib/schemas";

export type DiscordTestRoute = { route: DiscordRoute; label: string };

export const DISCORD_TEST_ROUTE_SECTIONS: {
  title: string;
  routes: DiscordTestRoute[];
}[] = [
  {
    title: "Stocks",
    routes: [
      { route: "stock_1m", label: "1m" },
      { route: "stock_5m", label: "5m" },
      { route: "stock_15m", label: "15m" },
      { route: "stock_30m", label: "30m" },
      { route: "stock_1h", label: "1h" },
      { route: "stock_1d", label: "1d" },
      { route: "stock_1w", label: "1w" },
    ],
  },
  {
    title: "Crypto",
    routes: [
      { route: "crypto_1m", label: "1m" },
      { route: "crypto_5m", label: "5m" },
      { route: "crypto_15m", label: "15m" },
      { route: "crypto_30m", label: "30m" },
      { route: "crypto_1h", label: "1h" },
      { route: "crypto_1d", label: "1d" },
      { route: "crypto_1w", label: "1w" },
    ],
  },
  {
    title: "News",
    routes: [
      { route: "stock_news", label: "Stock news" },
      { route: "crypto_news", label: "Crypto news" },
    ],
  },
];
