"""Fundamentals + news bot: daily fundamentals snapshots, news poll, daily analyst digest.

Run in its own terminal:  python fundabot.py

Watches every symbol in every watchlist of output/signal_config.json (same config
the Signals tab edits — no restart needed):
  - Fundamentals: one snapshot refresh per NY trading day (after 16:10 ET) plus
    a catch-up refresh of stale snapshots at boot. Provider free-tier quotas are
    tracked in output/funda_state.json and shared with the dashboard.
  - News: polled on NY weekdays an hour before the open (08:30), hourly while the
    market is open (pushes only stories not sent yet) and just after the close
    (16:05); pushed to the stock_news / crypto_news Discord channels, deduped
    across polls and providers (cold start drains the cached backlog in batches).
  - Fundi analyst digest: once per NY day (same 16:10 ET window) each symbol
    gets ONE message with its full fundamentals snapshot plus its latest news.

Logs: logs/fundabot.log. Stop with Ctrl+C.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import core.config as config
import integrations.funda_client as funda_client
import integrations.news_client as news_client
import integrations.news_summary as news_summary
import signaling.signals as signals
from market.market_symbols import is_bybit_crypto, normalize_symbol

NY = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fundabot")
_handler = RotatingFileHandler(
    config.LOG_DIR / "fundabot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)

WAKE_SECONDS = 60
FUNDA_AFTER_HOUR = 16        # NY hour after the close
FUNDA_AFTER_MIN = 10

NEWS_PREOPEN = (8, 30)       # NY time, an hour before the 09:30 open
NEWS_OPEN = (9, 30)
NEWS_CLOSE = (16, 0)
NEWS_POSTCLOSE = (16, 5)
NEWS_INTRADAY_MINUTES = 60

STATE_LOCK = threading.Lock()


def _load_pass_state() -> dict:
    state = funda_client._load_state()
    passes = state.get("passes")
    return passes if isinstance(passes, dict) else {}


def _save_pass_state(passes: dict) -> None:
    with STATE_LOCK:
        state = funda_client._load_state()
        state["passes"] = passes
        funda_client._save_state(state)


def watch_items_from_config(cfg: dict) -> tuple[list[str], list[str]]:
    """(stock_symbols, crypto_symbols) union across every watchlist, normalized."""
    stocks: list[str] = []
    cryptos: list[str] = []
    for watchlist in cfg.get("lists", []):
        for raw in watchlist.get("symbols", []):
            symbol = normalize_symbol(raw)
            if is_bybit_crypto(symbol):
                if symbol not in cryptos:
                    cryptos.append(symbol)
            elif symbol and symbol not in stocks:
                stocks.append(symbol)
    return stocks, cryptos


def write_heartbeat(stocks: int, cryptos: int, passes: dict) -> None:
    signal_style_payload = {
        "ts": datetime.now(NY).isoformat(timespec="seconds"),
        "stocks": stocks,
        "crypto": cryptos,
        "last_funda": passes.get("last_funda"),
        "last_funda_date": passes.get("last_funda_date"),
        "next_news": passes.get("next_news"),
    }
    funda_client._atomic_write(config.FUNDA_HEARTBEAT_PATH, json.dumps(signal_style_payload, indent=1))


def run_fundamentals_pass(stocks: list[str], passes: dict, *, force: bool = False) -> int:
    """Refresh every stock symbol. Returns how many were refreshed."""
    if not stocks:
        return 0
    done = 0
    for symbol in stocks:
        try:
            snap_before = funda_client.read_snapshot(symbol)
            fresh = funda_client.is_fresh(snap_before)
            if fresh and not force:
                continue
            snap = funda_client.refresh_symbol(symbol, force=force)
            if snap is not None and (force or not fresh):
                done += 1
                coverage = len(snap.get("fields", {}))
                who = ",".join(snap.get("sources_used", [])) or "-"
                logger.info("funda %s: %d fields via %s", symbol, coverage, who)
        except Exception as exc:  # noqa: BLE001 — never let one symbol break the pass
            logger.warning("funda %s failed: %s", symbol, exc)
    passes["last_funda"] = datetime.now(NY).isoformat(timespec="seconds")
    passes["last_funda_date"] = datetime.now(NY).strftime("%Y-%m-%d")
    _save_pass_state(passes)
    logger.info("fundamentals pass done: %d refreshed of %d", done, len(stocks))
    return done


def update_crypto_snapshots(cryptos: list[str]) -> None:
    for symbol in cryptos:
        try:
            funda_client.refresh_crypto(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("crypto snapshot %s failed: %s", symbol, exc)


def next_news_time(now: datetime) -> datetime:
    """Next news slot after `now`: pre-open, hourly while open, post-close; weekdays only."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(8):
        if day.weekday() < 5:
            slots = [day.replace(hour=NEWS_PREOPEN[0], minute=NEWS_PREOPEN[1])]
            t = day.replace(hour=NEWS_OPEN[0], minute=NEWS_OPEN[1])
            close = day.replace(hour=NEWS_CLOSE[0], minute=NEWS_CLOSE[1])
            while t < close:
                slots.append(t)
                t += timedelta(minutes=NEWS_INTRADAY_MINUTES)
            slots.append(day.replace(hour=NEWS_POSTCLOSE[0], minute=NEWS_POSTCLOSE[1]))
            for slot in slots:
                if slot > now:
                    return slot
        day += timedelta(days=1)
    return now + timedelta(hours=4)


def push_news_pass(stocks: list[str], cryptos: list[str], passes: dict) -> None:
    """Poll providers, push all cached-but-unsent items, then mark them sent.

    Candidates are the latest NEWS_MAX_PER_SYMBOL cached items per symbol —
    deduping against output/news_sent.json makes this safe: on a cold start it
    drains the backlog per pass (up to NEWS_MAX_PER_POLL overall), later passes
    only carry stories fetched since.
    """
    routes = {"stocks": "stock_news", "crypto": "crypto_news"}
    pushed_total = 0
    pushed_ids: list[str] = []
    sent = news_client.sent_ids()
    # With summaries on, nothing goes out without one: when LM Studio is down the
    # queue waits for the next pass instead of falling back to English headlines.
    hold = config.NEWS_LLM_SUMMARY and not news_summary.llm_up()
    if hold:
        logger.warning("LM Studio not reachable — holding news until the next pass")

    for group, symbols in (("stocks", stocks), ("crypto", cryptos)):
        route = routes[group]
        webhook = signals.discord_webhook_url(route)
        if not webhook:
            logger.warning("no webhook configured for %s — skipping %s", route, group)
            continue
        news_client.poll_many(symbols)  # refresh caches first
        for symbol in symbols:
            if pushed_total >= config.NEWS_MAX_PER_POLL:
                break
            items = news_client.read_symbol_news(symbol)[: config.NEWS_MAX_PER_SYMBOL]
            due = [it for it in items if it["id"] not in sent]
            if not due:
                continue
            logger.info("news queue %s: %d item(s) to push", symbol, len(due))
            for item in due:
                if hold or pushed_total >= config.NEWS_MAX_PER_POLL:
                    break
                if config.NEWS_LLM_SUMMARY:
                    text = news_summary.summary_message(item)
                    if not text:
                        logger.warning("news summary failed %s id=%s — held for next pass", symbol, item["id"])
                        continue
                else:
                    text = news_client.push_text(item)
                ok = signals.dc_send(webhook, text=text, suppress_embeds=True)
                if not ok:
                    logger.warning("discord push failed %s id=%s (not marked sent)", symbol, item["id"])
                    continue
                sent.add(item["id"])
                pushed_ids.append(item["id"])
                pushed_total += 1
                logger.info("news push %s: %s", symbol, item["title"][:80])

    if pushed_ids:
        news_client.mark_sent(pushed_ids)
        logger.info("news poll: pushed %d item(s)", pushed_total)
    else:
        logger.info("news poll: no new items to push")

    passes["next_news"] = next_news_time(datetime.now(NY)).isoformat(timespec="seconds")
    _save_pass_state(passes)


def due_for_daily_fundamentals(passes: dict, now: datetime) -> bool:
    if passes.get("last_funda_date") == now.strftime("%Y-%m-%d"):
        return False
    if now.hour > FUNDA_AFTER_HOUR or (now.hour == FUNDA_AFTER_HOUR and now.minute >= FUNDA_AFTER_MIN):
        return True
    # First boot with nothing fetched yet: fill immediately.
    return not passes.get("last_funda")


# ------------------------------------------------------------------ daily analyst digest
#
# Once per NY day (same after-close window as the fundamentals pass) each
# watchlist symbol gets ONE Discord message with everything we know: the full
# fundamentals snapshot + its latest news. Crypto symbols are included, routed
# to the crypto_news channel.

DIGEST_MAX_NEWS = 4
DIGEST_STATE_PATH = config.FUNDA_DIR / "funda_digest.json"
DIGEST_LOCK = threading.Lock()

STOCK_ROWS = (
    ("Price", "price"), ("Prev close", "prev_close"), ("Change %", "change_pct"),
    ("Mcap", "mcap"), ("Shares", "shares_out"), ("Vol avg", "avg_volume"),
    ("52w low", "week52_low"), ("52w high", "week52_high"),
    ("P/E", "pe"), ("fwd P/E", "forward_pe"), ("PEG", "peg"),
    ("Div yield", "div_yield"), ("Beta", "beta"),
    ("EPS TTM", "eps_ttm"), ("Rev TTM", "revenue_ttm"),
    ("NI TTM", "net_income_ttm"), ("FCF TTM", "fcf_ttm"),
    ("Gross marg", "gross_margin"), ("Op marg", "operating_margin"),
    ("Net marg", "net_margin"), ("Rev growth YoY", "revenue_growth_yoy"),
    ("ROE", "roe"), ("D/E", "de_ratio"), ("Current ratio", "current_ratio"),
    ("Next earnings", "next_earnings"), ("EPS est", "next_eps_estimate"),
)

PERCENT_FIELDS = {
    "change_pct", "div_yield", "gross_margin", "operating_margin", "net_margin",
    "revenue_growth_yoy", "roe",
    "change_24h_pct", "change_7d_pct", "change_30d_pct", "ath_change_pct",
}

# fields whose sign carries meaning (+X% / -X%); anything else just shows the number
SIGNED_PCT_FIELDS = {
    "change_pct", "revenue_growth_yoy",
    "change_24h_pct", "change_7d_pct", "change_30d_pct", "ath_change_pct",
}


def _nfmt(v):
    """Humanize a numeric snapshot value (5114261000000 -> 5.11T)."""
    if v is None:
        return "–"
    if isinstance(v, str):
        return v
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:,.2f}"


def _nrow(snap: dict, label: str, field: str) -> str:
    fld = snap.get("fields", {}).get(field)
    if not fld or fld.get("v") is None:
        return ""
    v = fld["v"]
    unit = "%" if field in PERCENT_FIELDS else ""
    sign = "+" if field in SIGNED_PCT_FIELDS and isinstance(v, (int, float)) and v > 0 else ""
    return f"{label} {sign}{_nfmt(v)}{unit}"


def _digest_header(symbol: str, snap: dict) -> str:
    f = snap["fields"]
    name = f.get("name", {}).get("v")
    parts = [f"**📊 Fundi analyst — {symbol}**"]
    sub = []
    if name and name != symbol:
        sub.append(str(name))
    if snap.get("asset") == "stock":
        sector = f.get("sector", {}).get("v")
        industry = f.get("industry", {}).get("v")
        if sector:
            sub.append(f"{sector}" + (f" / {industry}" if industry else ""))
    if sub:
        parts.append(f"*{' · '.join(str(x) for x in sub)}*")
    return "\n".join(parts)


def _digest_body(snap: dict) -> str:
    rows = []
    for label, field in STOCK_ROWS:
        row = _nrow(snap, label, field)
        if row:
            rows.append(row)
    if not rows:
        return ""
    # two compact rows: half+half, joined by middle dots
    half = (len(rows) + 1) // 2
    left = " · ".join(rows[:half])
    right = " · ".join(rows[half:])
    return "\n".join(x for x in (left, right) if x)


def _digest_news(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["**📰 Latest news**"]
    for it in items[:DIGEST_MAX_NEWS]:
        ts = datetime.fromisoformat(it["ts"]).astimezone(NY)
        sent = f" {'▲' if (it.get('sentiment') or 0) >= 0 else '▼'}{it['sentiment']:+.2f}" if it.get("sentiment") is not None else ""
        title = str(it["title"])[:110]
        lines.append(f"• {ts:%b %d %H:%M} — {it['source']}{sent} — {title}")
    return "\n".join(lines)


def digest_text(symbol: str, snap: dict, items: list[dict]) -> str:
    parts = [_digest_header(symbol, snap)]
    body = _digest_body(snap)
    if body:
        parts.append(body)
    news = _digest_news(items)
    if news:
        parts.append(news)
    return "\n\n".join(p for p in parts if p)


def _split_discord(text: str, limit: int = 1900) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines():
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur.strip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks or [""]


def send_fundi_digest_pass(stocks: list[str], cryptos: list[str], *, force: bool = False) -> int:
    """Send the per-symbol analyst digest. Returns how many symbols were sent."""
    with DIGEST_LOCK:
        last_date = None
        if DIGEST_STATE_PATH.exists():
            try:
                last_date = json.loads(DIGEST_STATE_PATH.read_text(encoding="utf-8")).get("last_date")
            except (json.JSONDecodeError, OSError):
                last_date = None
        today = datetime.now(NY).strftime("%Y-%m-%d")
        if last_date == today and not force:
            return 0

        routes = {"stocks": "stock_news", "crypto": "crypto_news"}
        sent_symbols = 0
        for group, symbols in (("stocks", stocks), ("crypto", cryptos)):
            webhook = signals.discord_webhook_url(routes[group])
            if not webhook:
                continue
            for symbol in symbols:
                snap = funda_client.read_snapshot(symbol)
                if not snap:
                    continue
                items = news_client.read_symbol_news(symbol)
                text = digest_text(symbol, snap, items)
                if not text:
                    continue
                ok = all(
                    signals.dc_send(webhook, text=chunk)
                    for chunk in _split_discord(text)
                )
                if ok:
                    sent_symbols += 1
                else:
                    logger.warning("digest send failed for %s", symbol)
        state = {"last_date": today, "sent_symbols": sent_symbols,
                 "ts": datetime.now(NY).isoformat(timespec="seconds")}
        funda_client._atomic_write(DIGEST_STATE_PATH, json.dumps(state, indent=1))
        logger.info("fund analyst digest: sent %d symbol message(s)", sent_symbols)
        return sent_symbols


def due_for_digest(now: datetime) -> bool:
    if DIGEST_STATE_PATH.exists():
        try:
            state = json.loads(DIGEST_STATE_PATH.read_text(encoding="utf-8"))
            if state.get("last_date") == now.strftime("%Y-%m-%d"):
                return False
        except (json.JSONDecodeError, OSError):
            pass
    if now.hour > FUNDA_AFTER_HOUR or (now.hour == FUNDA_AFTER_HOUR and now.minute >= FUNDA_AFTER_MIN):
        return True
    # days with a fundamentals pass still pending (e.g. first boot) send ASAP
    return False


def due_for_news(passes: dict, now: datetime) -> bool:
    return not passes.get("next_news") or now >= datetime.fromisoformat(passes["next_news"])


def main() -> None:
    logger.info("Starting fundabot (fundamentals snapshots + news poller)")
    passes = _load_pass_state()
    while True:
        wake_start = time.time()
        now = datetime.now(NY)
        cfg = signals.load_config()
        signals.reload_discord_webhooks_cache()
        stocks, cryptos = watch_items_from_config(cfg)

        if due_for_daily_fundamentals(passes, now):
            run_fundamentals_pass(stocks, passes)

        if not passes.get("boot_filled"):
            stale = [s for s in stocks if not funda_client.is_fresh(funda_client.read_snapshot(s))]
            if stale:
                logger.info("boot catch-up: %d stale symbols", len(stale))
                run_fundamentals_pass(stocks, passes)
            passes["boot_filled"] = True
            _save_pass_state(passes)

        if due_for_news(passes, now):
            if cryptos:
                update_crypto_snapshots(cryptos)
            push_news_pass(stocks, cryptos, passes)

        if due_for_digest(now):
            if cryptos:
                update_crypto_snapshots(cryptos)
            send_fundi_digest_pass(stocks, cryptos)

        write_heartbeat(len(stocks), len(cryptos), passes)

        logger.info(
            "wake: %d stocks, %d crypto | funda=%s next_news=%s | %.1fs",
            len(stocks),
            len(cryptos),
            passes.get("last_funda_date", "-"),
            (passes.get("next_news") or "")[:16],
            time.time() - wake_start,
        )
        time.sleep(WAKE_SECONDS)


if __name__ == "__main__":
    main()
