"""Finance news: multi-provider free-tier fetcher with URL-based dedupe.

Canonical item: {id, ts, symbol, title, summary, url, source, sentiment}
  id = sha1(url) — dedupes the same story across providers.
  sentiment = nullable float (-1..1) when the provider scores it (Alpha Vantage).

Providers (best -> worst, all free tiers):
  sec_8k        no key                the company's 8-K filings (last 30 days), tagged by item
                                      (earnings, leadership, deal, restatement, bankruptcy, ...)
  finnhub      FINNHUB_API_KEY       company-news (60/min, effectively unlimited)
  alpaca        ALPACA_API_KEY/SECRET existing keys; v1beta1 news (needs entitlement)
  google_rss    no key                per-symbol keyword search fallback
  crypto_rss    no key                CoinDesk/Cointelegraph/Decrypt filtered per coin

Per-symbol caches: output/news/<symbol>.json
Pushed/dedupe state: output/news_sent.json (rolling hash window)
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from funda_client import (
    NY,
    _atomic_write,
    _get_json,
    _get_text,
    _num,
)

HTTP_TIMEOUT = 20


# ------------------------------------------------------------------ helpers

def _item_id(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8", "ignore")).hexdigest()[:16]


def _clean_item(symbol: str, *, title, url, ts, summary=None, source=None,
                sentiment=None, tags=None) -> dict | None:
    title = (title or "").strip()
    url = (url or "").strip()
    if not title or not url or not ts:
        return None
    if not str(url).startswith("http"):
        return None
    item = {
        "id": _item_id(url),
        "symbol": symbol,
        "ts": ts.isoformat("T", timespec="seconds"),
        "title": title[:220],
        "summary": (summary or "").strip()[:200] or None,
        "url": url,
        "source": (source or "").strip()[:40] or None,
        "sentiment": _num(sentiment),
    }
    if tags:
        item["tags"] = list(tags)
    return item


# ------------------------------------------------------------------ sec 8-K filings

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_8K_DAYS = 30
# 8-K item -> (tag, short label). 9.01 (exhibits) rides along with most filings and is skipped.
EIGHT_K_ITEMS = {
    "1.01": ("deal", "Material agreement"),
    "1.02": ("deal", "Agreement terminated"),
    "1.03": ("bankruptcy", "Bankruptcy or receivership"),
    "1.05": ("cyber", "Cybersecurity incident"),
    "2.01": ("deal", "Acquisition or disposition completed"),
    "2.02": ("earnings", "Results of operations"),
    "2.03": ("financing", "New debt obligation"),
    "2.04": ("financing", "Debt acceleration trigger"),
    "2.05": ("restructuring", "Exit or restructuring costs"),
    "2.06": ("impairment", "Material impairment"),
    "3.01": ("delisting", "Delisting or listing-rule notice"),
    "3.02": ("dilution", "Unregistered equity sale"),
    "3.03": ("shareholder_rights", "Change to shareholder rights"),
    "4.01": ("auditor", "Auditor change"),
    "4.02": ("restatement", "Prior financials no longer reliable"),
    "5.01": ("control_change", "Change in control"),
    "5.02": ("leadership", "Officer or director change"),
    "5.03": ("governance", "Charter or bylaws amended"),
    "5.07": ("vote", "Shareholder vote results"),
    "7.01": ("reg_fd", "Reg FD disclosure"),
    "8.01": ("other", "Other material event"),
}


def tag_8k_items(items: str) -> tuple[list[str], list[str]]:
    """'2.02,9.01' -> (['earnings'], ['Results of operations'])."""
    tags, labels = [], []
    for code in str(items or "").replace(" ", "").split(","):
        if code in EIGHT_K_ITEMS:
            tag, label = EIGHT_K_ITEMS[code]
            if tag not in tags:
                tags.append(tag)
            labels.append(label)
    return tags, labels


def sec_8k_items_from_submissions(symbol: str, cik: int, data: dict, now: datetime | None = None) -> list[dict]:
    """News items for the 8-K filings of the last SEC_8K_DAYS in a submissions payload."""
    recent = ((data or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    cutoff = (now or datetime.now(NY)) - timedelta(days=SEC_8K_DAYS)
    out: list[dict] = []
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A"):
            continue
        try:
            accepted = datetime.fromisoformat(str(recent["acceptanceDateTime"][i]).replace("Z", "+00:00"))
            accession = str(recent["accessionNumber"][i])
            doc = str(recent["primaryDocument"][i])
            items = str((recent.get("items") or [""] * len(forms))[i])
        except (KeyError, IndexError, ValueError):
            continue
        if accepted < cutoff:
            continue
        tags, labels = tag_8k_items(items)
        if not labels:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"
        it = _clean_item(
            symbol, title=f"{form}: {'; '.join(labels)}", url=url, ts=accepted.astimezone(NY),
            summary=f"Items {items}", source="SEC EDGAR", tags=tags,
        )
        if it:
            out.append(it)
    return out


def _sec_8k_items(symbol: str) -> list[dict]:
    from funda_client import _edgar_cik

    cik = _edgar_cik(symbol)
    if not cik:
        return []
    data, err = _get_json(SEC_SUBMISSIONS_URL.format(cik=cik), per_minute=None)
    if err or not isinstance(data, dict):
        return []
    return sec_8k_items_from_submissions(symbol, cik, data)


# ------------------------------------------------------------------ finnhub

def _finnhub_items(symbol: str) -> list[dict]:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return []
    end = datetime.now(NY).date()
    frm = (end - timedelta(days=3)).isoformat()
    data, err = _get_json(
        "https://finnhub.io/api/v1/company-news",
        params={"token": key, **{"symbol": symbol, "from": frm, "to": end.isoformat()}},
        per_minute=50,
    )
    if err or not isinstance(data, list):
        return []
    items = []
    for row in data[:10]:
        ts = datetime.fromtimestamp(int(row.get("datetime", 0)), tz=NY) if row.get("datetime") else None
        it = _clean_item(
            symbol, title=row.get("headline"), url=row.get("url"), ts=ts,
            summary=row.get("summary"), source=row.get("source"),
        )
        if it:
            items.append(it)
    return items


# ------------------------------------------------------------------ fmp
# (FMP stock news dropped here: /stable/news/stock-latest is paywalled on the
# free plan — the adapter was removed rather than burning poll time.)


# ------------------------------------------------------------------ alpaca

def _alpaca_items(symbol: str) -> list[dict]:
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not (key and secret):
        return []
    data, _ = _get_json(
        "https://data.alpaca.markets/v1beta1/news",
        params={"symbols": symbol, "limit": 8},
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
    )
    if not isinstance(data, dict):
        return []
    items = []
    for row in (data.get("news") or [])[:8]:
        try:
            ts = datetime.fromisoformat(str(row.get("created_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        src = (row.get("source") or "alpaca")
        src_name = src if isinstance(src, str) else (src.get("name") if isinstance(src, dict) else None)
        it = _clean_item(
            symbol, title=row.get("headline"), url=row.get("url"), ts=ts,
            summary=row.get("summary"), source=src_name,
        )
        if it:
            items.append(it)
    return items


# (Alpha Vantage dropped here: NEWS_SENTIMENT went premium-only on their free
# tier — the adapter was removed rather than wasting quota on refusals.)


# ------------------------------------------------------------------ google news rss

_GOOGLE_RSS_URL = "https://news.google.com/rss/search"


def _google_news_items(symbol: str, company: str | None) -> list[dict]:
    query = f"{symbol} stock" + (f" OR \"{company}\"" if company else "")
    data, err = _get_text(_GOOGLE_RSS_URL, params={
        "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
    })
    if err or data is None:
        return []
    return _parse_rss(data, symbol, max_items=8)


GEOPOLITICAL_QUERIES = (
    "US Israel Iran military",
    "oil prices",
)


def geopolitical_headlines(max_items: int = 8) -> list[dict]:
    """Analyst macro block: Google News RSS titles for geopolitical/oil queries.

    Title-only rows on purpose - the LLM judges risk-off, we never score them.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for query in GEOPOLITICAL_QUERIES:
        data, err = _get_text(_GOOGLE_RSS_URL, params={
            "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
        })
        if err or data is None:
            continue
        for it in _parse_rss(data, "MACRO", max_items=6):
            title = it["title"]
            if title in seen:
                continue
            seen.add(title)
            out.append({"ts": it["ts"], "source": it.get("source"), "title": title})
            if len(out) >= max_items:
                return out
    return out


def _parse_rss(body: bytes | str, symbol: str, max_items: int = 8) -> list[dict]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    items = []
    for entry in root.iter("item"):
        if len(items) >= max_items:
            break
        title = (entry.findtext("title") or "").strip()
        link = (entry.findtext("link") or "").strip()
        raw_ts = (entry.findtext("pubDate") or "").strip()
        desc = (entry.findtext("description") or "").strip()
        try:
            ts = email.utils.parsedate_to_datetime(raw_ts)
        except (TypeError, ValueError):
            continue
        # Google News appends " - Publisher" to the title; strip the suffix when
        # it plausibly is a publisher (short, title stays substantial).
        title, pub = title, ""
        if " - " in title:
            base, tail = title.rsplit(" - ", 1)
            if len(base) >= 10 and len(tail) <= 40:
                title, pub = base, tail
        it = _clean_item(symbol, title=title, url=link, ts=ts, summary=desc,
                         source=pub)
        if it:
            items.append(it)
    return items


# ------------------------------------------------------------------ crypto rss

_CRYPTO_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
}

_COIN_ALIASES = {
    "BTC-USD": ("bitcoin", "btc "),
    "ETH-USD": ("ethereum", "ether", "eth "),
    "SOL-USD": ("solana", "sol "),
    "XRP-USD": ("xrp", "ripple"),
    "ADA-USD": ("cardano", "ada "),
    "DOGE-USD": ("dogecoin", "doge"),
    "BNB-USD": ("bnb ", "binance coin", "binance coin"),
    "AVAX-USD": ("avalanche", "avax"),
    "DOT-USD": ("polkadot", "dot "),
    "LINK-USD": ("chainlink", "link "),
    "LTC-USD": ("litecoin", "ltc "),
}


def _crypto_news_items(symbol: str, alias_cache: dict[str, tuple]) -> list[dict]:
    aliases = alias_cache.get(symbol)
    if not aliases:
        aliases = _COIN_ALIASES.get(symbol.upper()) or (
            (symbol.split("-")[0].lower(), symbol.split("-")[0].upper() + " ")
        )
        alias_cache[symbol] = aliases
    items = []
    seen: set[str] = set()
    for feed_name, feed_url in _CRYPTO_FEEDS.items():
        data, _ = _get_text(feed_url)
        if data is None:
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        for entry in root.iter("item"):
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            desc = (entry.findtext("description") or "").strip()
            raw_ts = (entry.findtext("pubDate") or "").strip()
            try:
                ts = email.utils.parsedate_to_datetime(raw_ts)
            except (TypeError, ValueError):
                continue
            hay = f"{title} {desc}".lower()
            if not any(a.lower() in hay for a in aliases):
                continue
            it = _clean_item(symbol, title=title, url=link, ts=ts, summary=desc, source=feed_name)
            if it and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:8]


# ------------------------------------------------------------------ caches + sent state

def _cache_path(symbol: str):
    return config.NEWS_DIR / f"{symbol.replace('=', '_').replace(':', '_')}.json"


def read_symbol_news(symbol: str) -> list[dict]:
    path = _cache_path(symbol)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_symbol_news(symbol: str, items: list[dict]) -> None:
    keep = items[: config.NEWS_KEEP_PER_SYMBOL]
    _atomic_write(_cache_path(symbol), json.dumps(
        {"symbol": symbol, "updated": datetime.now(NY).isoformat(timespec="seconds"), "items": keep},
        indent=1,
    ))


def _sent_state() -> dict:
    if config.NEWS_SENT_PATH.exists():
        try:
            data = json.loads(config.NEWS_SENT_PATH.read_text(encoding="utf-8"))
            sent = data.get("sent")
            if isinstance(sent, dict):
                return {"sent": sent}
        except (json.JSONDecodeError, OSError):
            pass
    return {"sent": {}}


def sent_ids() -> set[str]:
    return set(_sent_state()["sent"].keys())


_sent_lock = threading.Lock()


def mark_sent(ids: list[str]) -> None:
    """Record pushed item ids in the rolling dedupe window."""
    with _sent_lock:
        state = _sent_state()
        sent = state["sent"]
        now = datetime.now(NY).isoformat(timespec="seconds")
        for item_id in ids:
            sent[item_id] = now
        if len(sent) > config.NEWS_MAX_LOG:
            keep_ids = [k for k, _ in sorted(sent.items(), key=lambda kv: kv[1])[-config.NEWS_MAX_LOG:]]
            sent = {k: sent[k] for k in keep_ids}
        _atomic_write(config.NEWS_SENT_PATH, json.dumps({"sent": sent}, indent=1))


# ------------------------------------------------------------------ poll

def poll_symbol(symbol: str, company: str | None = None) -> tuple[list[dict], list[dict]]:
    """Fetch fresh items for one symbol. Returns (all_items, new_items)."""
    is_crypto = symbol.upper().endswith("-USD")
    known = {it["id"] for it in read_symbol_news(symbol)}
    got: dict[str, dict] = {}

    if is_crypto:
        for it in _crypto_news_items(symbol, _ALIAS_RUN_CACHE):
            got.setdefault(it["id"], it)
    else:
        for fetch in (_sec_8k_items, _finnhub_items, _alpaca_items,
                      lambda s: _google_news_items(s, company)):
            for it in fetch(symbol):
                got.setdefault(it["id"], it)

    all_items = sorted(
        got.values(), key=lambda x: x["ts"], reverse=True
    )
    new_items = [it for it in all_items if it["id"] not in known]
    _write_symbol_news(symbol, all_items)
    return all_items, new_items


_ALIAS_RUN_CACHE: dict[str, tuple] = {}


def poll_many(symbols: list[str], companies: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """Fetch all watchlist symbols. Returns {symbol: new_items} (only the new ones)."""
    companies = companies or {}
    out: dict[str, list[dict]] = {}
    for symbol in symbols:
        try:
            _, new_items = poll_symbol(symbol, companies.get(symbol))
            out[symbol] = new_items[: config.NEWS_MAX_PER_SYMBOL]
        except Exception:  # noqa: BLE001 — one symbol failing must not stop the poll
            out[symbol] = []
    return out


def read_all_news(symbol: str | None = None, limit: int = 60) -> list[dict]:
    """Merged recent items for the dashboard."""
    if symbol:
        return read_symbol_news(symbol)[:limit]
    rows: list[dict] = []
    for path in config.NEWS_DIR.glob("*.json"):
        if path.name.startswith("sec_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, list):
                rows.extend(items)
        except (json.JSONDecodeError, OSError):
            continue
    rows.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return rows[:limit]


def push_text(item: dict) -> str:
    """Discord/Telegram message for one news item."""
    try:
        shown_ts = datetime.fromisoformat(item["ts"]).astimezone(NY).strftime("%b %d %H:%M")
    except (ValueError, TypeError):
        shown_ts = item.get("ts", "")
    lines = [f"{item['symbol']} · {shown_ts} — {item['title']}"]
    if item.get("summary"):
        lines.append(item["summary"][:180])
    meta = []
    if item.get("tags"):
        meta.append(" ".join(f"#{t}" for t in item["tags"]))
    if item.get("source"):
        meta.append(item["source"])
    if isinstance(item.get("sentiment"), (int, float)):
        s = float(item["sentiment"])
        arrow = "↑" if s > 0.05 else ("↓" if s < -0.05 else "→")
        meta.append(f"sentiment {arrow} {s:+.2f}")
    if meta:
        lines.append(" · ".join(meta))
    lines.append(item["url"])
    return "\n".join(lines)
