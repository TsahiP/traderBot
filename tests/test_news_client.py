"""news_client: RSS parsing, dedupe state, poll flow, push formatting."""
import json
from datetime import datetime, timedelta
from email.utils import format_datetime

import pytest

import config
import news_client as nc


@pytest.fixture(autouse=True)
def _isolated_paths(monkeypatch, tmp_path):
    funda = tmp_path / "funda"
    news = tmp_path / "news"
    funda.mkdir()
    news.mkdir()
    monkeypatch.setattr(config, "FUNDA_DIR", funda)
    monkeypatch.setattr(config, "NEWS_DIR", news)
    monkeypatch.setattr(config, "FUNDA_STATE_PATH", tmp_path / "funda_state.json")
    monkeypatch.setattr(config, "NEWS_SENT_PATH", tmp_path / "news_sent.json")
    for name in ("FMP_API_KEY", "FINNHUB_API_KEY", "ALPHAVANTAGE_API_KEY",
                 "TWELVEDATA_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Apple beats guidance - Reuters</title>
<link>https://ex.com/a1</link>
<pubDate>{dt}</pubDate>
<description>Strong iPhone quarter.</description></item>
<item><title>Second item - Bloomberg</title>
<link>https://ex.com/a2</link>
<pubDate>{dt}</pubDate>
<description>Text.</description></item>
</channel></rss>
"""


def _now_dt() -> datetime:
    return datetime.now(nc.NY).replace(second=0, microsecond=0)


def test_parse_rss_strips_publisher_suffix():
    now = _now_dt()
    items = nc._parse_rss(RSS.format(dt=format_datetime(now)), "AAPL")
    assert [i["title"] for i in items] == ["Apple beats guidance", "Second item"]
    assert items[0]["source"] == "Reuters"
    assert items[0]["symbol"] == "AAPL"
    assert items[0]["summary"] == "Strong iPhone quarter."
    assert all(i["id"] and len(i["id"]) == 16 for i in items)


def test_clean_item_requires_usable_fields():
    assert nc._clean_item("AAPL", title="t", url="ftp://x", ts=_now_dt()) is None
    assert nc._clean_item("AAPL", title="", url="https://x", ts=_now_dt()) is None
    ok = nc._clean_item("AAPL", title="t", url="https://x", ts=_now_dt(),
                        summary="s", source="src", sentiment=0.7)
    assert ok["sentiment"] == 0.7
    assert nc._clean_item("AAPL", title="t", url="https://x", ts=_now_dt(),
                          sentiment="bad") is not None  # bad sentiment -> null, item kept


def test_sent_state_roundtrip():
    now = datetime.now(nc.NY).isoformat(timespec="seconds")
    config.NEWS_SENT_PATH.write_text(json.dumps({"sent": {"abc": now}}), encoding="utf-8")
    assert nc.sent_ids() == {"abc"}
    nc.mark_sent(["def"])
    assert nc.sent_ids() == {"abc", "def"}
    assert json.loads(config.NEWS_SENT_PATH.read_text(encoding="utf-8"))["sent"]["def"] == now


def test_poll_symbol_no_providers_configured_electronless(monkeypatch):
    """No keys at all -> google rss fallback fetches nothing, cache stays empty."""
    monkeypatch.setattr(nc, "_get_json", lambda *a, **k: (None, "offline"))
    monkeypatch.setattr(nc, "_get_text", lambda *a, **k: (None, "offline"))
    all_items, new_items = nc.poll_symbol("AAPL")
    assert all_items == new_items == []
    assert nc.read_symbol_news("AAPL") == []


def test_poll_symbol_new_items_detection(monkeypatch):
    now = _now_dt()

    def rss_only_get_text(url, *, params=None):
        if "google" in url:
            return RSS.format(dt=format_datetime(now)), None
        return None, "skipped"  # crypto feeds offline

    def json_offline(url, *args, **kwargs):
        return (None, "skipped")  # finnhub/fmp/alpaca/av offline

    monkeypatch.setattr(nc, "_get_text", rss_only_get_text)
    monkeypatch.setattr(nc, "_get_json", json_offline)
    _, first_new = nc.poll_symbol("AAPL")
    assert len(first_new) == 2
    second_all, second_new = nc.poll_symbol("AAPL")
    assert second_new == []  # same day -> nothing new


def test_push_text_formatting():
    ts = datetime(2026, 10, 23, 10, 30, tzinfo=nc.NY)
    text = nc.push_text({
        "symbol": "AAPL", "ts": ts.isoformat(),
        "title": "Apple beats", "summary": "Beat by a lot" * 40,
        "url": "https://ex.com/x", "source": "Reuters", "sentiment": 0.8,
    })
    assert "AAPL · Oct 23 10:30 — Apple beats" in text
    assert "sentiment ↑ +0.80" in text
    assert text.endswith("https://ex.com/x")


def test_crypto_alias_matching(monkeypatch):
    body = RSS.format(dt=format_datetime(_now_dt())).replace("Apple beats", "Bitcoin surges past $90k")
    monkeypatch.setattr(nc, "_get_text", lambda *a, **k: (body, None))
    items = nc._crypto_news_items("BTC-USD", {})
    assert len(items) == 1 and items[0]["symbol"] == "BTC-USD"
    # ETH story must not match the BTC symbol's aliases
    body_eth = RSS.format(dt=format_datetime(_now_dt())).replace("Apple beats", "Ethereum upgrade ships")
    monkeypatch.setattr(nc, "_get_text", lambda *a, **k: (body_eth, None))
    assert nc._crypto_news_items("BTC-USD", {}) == []


def test_read_all_news_merged(monkeypatch, tmp_path):
    now = _now_dt()
    old_ts = (now - timedelta(hours=5)).isoformat()
    new_ts = now.isoformat()
    for sym, ts in (("AAPL", new_ts), ("MSFT", old_ts)):
        config.NEWS_DIR.joinpath(f"{sym}.json").write_text(json.dumps(
            {"symbol": sym, "updated": now.isoformat(),
             "items": [{"id": sym, "symbol": sym, "title": f"{sym} story",
                        "url": f"https://ex.com/{sym}", "ts": ts}]},
        ), encoding="utf-8")
    rows = nc.read_all_news(limit=10)
    assert rows[0]["symbol"] == "AAPL"  # newest first
