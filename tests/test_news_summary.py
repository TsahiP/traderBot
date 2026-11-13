"""news_summary: article text extraction, fetch fallbacks, summary message."""
import pytest

import core.config as config
import integrations.news_summary as ns

LONG = "Nvidia shares rose after the company reported record data-center revenue this quarter. " * 6

HTML = f"""<html><body><nav><p>{'menu link ' * 20}</p></nav>
<article><p>{LONG}</p><p>short</p></article><footer><p>{'footer ' * 30}</p></footer></body></html>"""


def _item(**kw):
    item = {
        "id": "x", "symbol": "NVDA", "ts": "2026-10-27T11:50:00-04:00",
        "title": "Nvidia jumps", "url": "https://ex.com/a", "source": "Yahoo",
    }
    item.update(kw)
    return item


def test_extract_text_keeps_article_paragraphs_only():
    text = ns.extract_text(HTML)
    assert "record data-center revenue" in text
    assert "menu link" not in text and "footer" not in text
    assert "short" not in text  # paragraphs under 60 chars are dropped


class _Resp:
    def __init__(self, html="", status=200, ctype="text/html; charset=utf-8", url="https://ex.com/a"):
        self.text, self.status_code, self.url = html, status, url
        self.content = html.encode()
        self.headers = {"content-type": ctype}


def test_fetch_article_text_ok(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp(HTML))
    assert "record data-center revenue" in ns.fetch_article_text("https://ex.com/a")


@pytest.mark.parametrize("resp", [
    _Resp(HTML, status=403),
    _Resp(HTML, ctype="application/pdf"),
    _Resp("<html><body><p>tiny</p></body></html>"),      # paywall shell, too little text
    _Resp(HTML, url="https://www.sec.gov/Archives/x.htm"),  # redirected onto a skipped host
])
def test_fetch_article_text_unreadable_returns_empty(monkeypatch, resp):
    monkeypatch.setattr("requests.get", lambda *a, **k: resp)
    assert ns.fetch_article_text("https://ex.com/a") == ""


def test_fetch_skips_sec_without_request(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not fetch sec.gov")

    monkeypatch.setattr("requests.get", boom)
    assert ns.fetch_article_text("https://www.sec.gov/Archives/edgar/x.htm") == ""


def test_google_link_is_decoded_before_fetch(monkeypatch):
    seen = []
    monkeypatch.setattr(ns, "decode_google_url", lambda u: "https://real.example.com/story")
    monkeypatch.setattr("requests.get", lambda url, **k: seen.append(url) or _Resp(HTML, url=url))
    assert ns.fetch_article_text("https://news.google.com/rss/articles/CBMi123")
    assert seen == ["https://real.example.com/story"]


def test_google_link_undecodable_returns_empty(monkeypatch):
    monkeypatch.setattr(ns, "decode_google_url", lambda u: None)
    assert ns.fetch_article_text("https://news.google.com/rss/articles/CBMi123") == ""


def test_summary_message_is_symbol_hebrew_summary_and_link_only(monkeypatch):
    monkeypatch.setattr(ns, "fetch_article_text", lambda url: LONG)
    summary = "• נקודה\nהשפעה: חיובית על NVDA — צמיחה"
    monkeypatch.setattr(ns, "llm_chat_ex", lambda *a, **k: (summary, {"finish_reason": "stop"}))
    msg = ns.summary_message(_item(tags=["earnings"]))
    assert msg == "**NVDA**\n‏• נקודה\n‏השפעה: חיובית על NVDA — צמיחה\nhttps://ex.com/a"
    assert "Nvidia jumps" not in msg and "Yahoo" not in msg  # no English headline/source


def test_summary_message_falls_back_to_headline_when_article_unreadable(monkeypatch):
    monkeypatch.setattr(ns, "fetch_article_text", lambda url: "")
    seen = {}

    def fake(msgs, **kw):
        seen["user"] = msgs[1]["content"]
        return "• כותרת בלבד", {"finish_reason": "stop"}

    monkeypatch.setattr(ns, "llm_chat_ex", fake)
    msg = ns.summary_message(_item(summary="Blurb from the provider"))
    assert "Nvidia jumps" in seen["user"] and "Blurb from the provider" in seen["user"]
    assert "article not readable" in seen["user"]
    assert msg.startswith("**NVDA**\n‏• כותרת בלבד")


def test_summary_message_none_when_llm_fails_or_truncated(monkeypatch):
    monkeypatch.setattr(ns, "fetch_article_text", lambda url: LONG)

    def down(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ns, "llm_chat_ex", down)
    assert ns.summary_message(_item()) is None
    monkeypatch.setattr(ns, "llm_chat_ex", lambda *a, **k: ("cut off", {"finish_reason": "length"}))
    assert ns.summary_message(_item()) is None


def test_summary_uses_news_model_and_reasoning_off(monkeypatch):
    monkeypatch.setattr(config, "NEWS_LLM_MODEL", "my/news-model")
    seen = {}

    def fake(msgs, **kw):
        seen.update(kw, system=msgs[0]["content"])
        return "ok", {"finish_reason": "stop"}

    monkeypatch.setattr(ns, "llm_chat_ex", fake)
    assert ns.summarize_article(_item(), LONG) == "ok"
    assert seen["model"] == "my/news-model" and seen["reasoning_effort"] == "none"
    assert "Hebrew" in seen["system"] and "NVDA" in seen["system"]
