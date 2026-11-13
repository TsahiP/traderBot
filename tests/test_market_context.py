"""Macro context (QQQ, VIX, oil, ESLT, geopolitics) + news_client RSS helper."""
import json

import pandas as pd
import pytest

import market.market_context as mc
import integrations.news_client as nc


def _bars(closes, spread=0.5):
    idx = pd.date_range("2026-08-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        [
            {"Open": c, "High": c + spread, "Low": c - spread, "Close": c, "Volume": 1_000}
            for c in closes
        ],
        index=idx,
    )


_RISING = [100.0 + 0.4 * i for i in range(260)]
_FALLING = [112.0 - 0.3 * i for i in range(30)]


def _fake_fetch(counts, vix_exc=None):
    series = {
        "QQQ": _bars([100.0 + 40 * abs(((i % 50) - 25) / 25) ** 1.0 for i in range(260)]),
        "^VIX": _bars(_FALLING, 0.1),
        "CL=F": _bars([70.0 - 0.2 * i for i in range(30)]),
        "BZ=F": _bars([74.0 - 0.2 * i for i in range(30)]),
        "ESLT": _bars([90.0 + 0.2 * i for i in range(30)]),
    }

    def fake(symbol, **kwargs):
        counts.append(symbol)
        if symbol == "^VIX" and vix_exc:
            raise vix_exc("vix boom")
        return series[symbol]

    return fake


def test_market_context_trends_and_changes(monkeypatch):
    counts: list[str] = []
    monkeypatch.setattr(mc.market_data, "fetch_yfinance_history", _fake_fetch(counts))
    monkeypatch.setattr(nc, "geopolitical_headlines", lambda: [
        {"ts": "2026-10-24T09:00:00+00:00", "source": "Reuters",
         "title": "US warns Iran - Reuters"}
    ])
    ctx = mc.market_context(refresh=True)

    assert ctx["qqq"]["ok"] is True
    assert set(ctx["qqq"]["trend"]) == {"above_ema_20", "above_ema_50", "above_ema_200"}
    assert ctx["qqq"]["daily_pct"] is not None
    assert ctx["qqq"]["price_zone"] in ("premium", "discount", "n/a")
    # falling VIX: negative daily change
    assert ctx["vix"]["daily_pct"] < 0
    assert ctx["oil"]["wti"]["daily_pct"] < 0
    assert ctx["eslt"]["ok"] is True
    assert len(ctx["geopolitics"]["headlines"]) == 1
    assert "updated" in ctx


def test_market_context_ttl_cache(monkeypatch):
    counts: list[str] = []
    monkeypatch.setattr(mc.market_data, "fetch_yfinance_history", _fake_fetch(counts))
    monkeypatch.setattr(nc, "geopolitical_headlines", lambda: [])
    monkeypatch.setattr(mc.config, "MACRO_TTL_MIN", 10)

    mc.clear_cache()
    mc.market_context()
    first = len(counts)
    assert first == 5  # QQQ + VIX + WTI + Brent + ESLT
    mc.market_context()
    assert len(counts) == first        # served from cache
    mc.market_context(refresh=True)
    assert len(counts) == 2 * first    # forced refresh


def test_market_context_partial_failure_isolated(monkeypatch):
    counts: list[str] = []
    monkeypatch.setattr(
        mc.market_data, "fetch_yfinance_history", _fake_fetch(counts, RuntimeError)
    )
    monkeypatch.setattr(nc, "geopolitical_headlines", lambda: [])
    ctx = mc.market_context(refresh=True)
    assert ctx["vix"]["ok"] is False and "vix boom" in ctx["vix"]["error"]
    assert ctx["qqq"]["ok"] is True
    assert ctx["oil"]["wti"]["ok"] is True


_RSS_GEO = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>US threatens military response - Israel Times</title><link>https://n.example/1</link><pubDate>Tue, 20 Oct 2026 10:00:00 +0000</pubDate><description>geo</description></item>
<item><title>Oil jumps after flareup - Reuters</title><link>https://n.example/2</link><pubDate>Tue, 20 Oct 2026 08:00:00 +0000</pubDate><description>oil</description></item>
</channel></rss>"""

_RSS_OIL = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Oil jumps after flareup - Reuters</title><link>https://n.example/2</link><pubDate>Tue, 20 Oct 2026 08:00:00 +0000</pubDate><description>oil</description></item>
</channel></rss>"""


def test_geopolitical_headlines_dedupe(monkeypatch):
    queries: list[str] = []

    def fake_text(url, params=None):
        queries.append((params or {}).get("q", ""))
        if "Israel" in (params or {}).get("q", ""):
            return _RSS_GEO.decode("utf-8"), None
        return _RSS_OIL.decode("utf-8"), None

    monkeypatch.setattr(nc, "_get_text", fake_text)
    out = nc.geopolitical_headlines()
    assert len(queries) == len(nc.GEOPOLITICAL_QUERIES)
    assert len(out) == 2
    titles = [r["title"] for r in out]
    assert "US threatens military response" in titles[0]
    assert "Oil jumps after flareup" in titles[1]


def test_geopolitical_headlines_feed_failure(monkeypatch):
    monkeypatch.setattr(nc, "_get_text", lambda *a, **k: (None, "http 503"))
    assert nc.geopolitical_headlines() == []
