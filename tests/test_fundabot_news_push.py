"""fundabot.push_news_pass: a news story only goes to Discord with its summary."""
import fundabot as fb
import pytest


def _item(i):
    return {"id": f"id{i}", "symbol": "NVDA", "ts": "2026-10-31T10:00:00-04:00",
            "title": f"Story {i}", "url": f"https://ex.com/{i}"}


@pytest.fixture
def pusher(monkeypatch):
    sent, marked = [], []
    monkeypatch.setattr(fb.config, "NEWS_LLM_SUMMARY", True)
    monkeypatch.setattr(fb.signals, "discord_webhook_url", lambda route: "https://hook")
    monkeypatch.setattr(fb.signals, "dc_send", lambda url, text, **k: sent.append(text) or True)
    monkeypatch.setattr(fb.news_client, "sent_ids", lambda: set())
    monkeypatch.setattr(fb.news_client, "poll_many", lambda symbols, *a, **k: {})
    monkeypatch.setattr(fb.news_client, "read_symbol_news", lambda symbol: [_item(1), _item(2)])
    monkeypatch.setattr(fb.news_client, "mark_sent", lambda ids: marked.extend(ids))
    monkeypatch.setattr(fb, "_save_pass_state", lambda passes: None)
    return sent, marked


def test_news_held_while_llm_down(monkeypatch, pusher):
    sent, marked = pusher
    monkeypatch.setattr(fb.news_summary, "llm_up", lambda: False)
    monkeypatch.setattr(fb.news_summary, "summary_message", lambda item: pytest.fail("no LLM call"))
    monkeypatch.setattr(fb.news_client, "push_text", lambda item: pytest.fail("no plain headline"))
    fb.push_news_pass(["NVDA"], [], {})
    assert sent == [] and marked == []


def test_news_sent_only_with_summary(monkeypatch, pusher):
    sent, marked = pusher
    monkeypatch.setattr(fb.news_summary, "llm_up", lambda: True)
    monkeypatch.setattr(fb.news_summary, "summary_message",
                        lambda item: None if item["id"] == "id1" else f"**NVDA**\n{item['title']}")
    fb.push_news_pass(["NVDA"], [], {})
    assert sent == ["**NVDA**\nStory 2"]
    assert marked == ["id2"]


def _ny(day, hh, mm):
    return fb.datetime(2026, 11, day, hh, mm, tzinfo=fb.NY)  # Nov 2 2026 = Monday


@pytest.mark.parametrize("now,expected", [
    (_ny(2, 7, 0), _ny(2, 8, 30)),     # before the day's first slot -> an hour before the open
    (_ny(2, 8, 30), _ny(2, 9, 30)),    # then the open, hourly while trading
    (_ny(2, 10, 0), _ny(2, 10, 30)),
    (_ny(2, 15, 31), _ny(2, 16, 5)),   # last intraday slot is 15:30, then post-close
    (_ny(2, 16, 5), _ny(3, 8, 30)),
    (_ny(6, 16, 5), _ny(9, 8, 30)),    # Friday close -> Monday pre-open
])
def test_next_news_time(now, expected):
    assert fb.next_news_time(now) == expected
