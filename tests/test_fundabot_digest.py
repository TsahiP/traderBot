import fundabot as fb
import pytest


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(fb.config, "FUNDA_DIR", tmp_path / "funda")
    fb.config.FUNDA_DIR.mkdir(exist_ok=True)
    monkeypatch.setattr(fb, "DIGEST_STATE_PATH", fb.config.FUNDA_DIR / "funda_digest.json")


def _mk_snap(**fields):
    return {
        "symbol": "AAPL", "asset": "stock",
        "fields": {k: {"v": v, "src": "test", "ts": "t"} for k, v in fields.items() if v is not None},
        "quarters": [], "sources_used": [], "errors": [],
    }


def test_digest_text_header_and_rows():
    snap = _mk_snap(name="Apple Inc.", sector="Technology", price=212.2, pe=26.8,
                    revenue_ttm=416_000_000_000, gross_margin=50.05, change_pct=-3.08,
                    next_earnings="2026-11-17")
    text = fb.digest_text("AAPL", snap, [])
    assert "**📊 Fundi analyst — AAPL**" in text
    assert "Apple Inc." in text and "Technology" in text
    assert "Price 212.20" in text
    assert "P/E 26.80" in text
    assert "Rev TTM 416.00B" in text
    assert "Gross marg 50.05%" in text
    assert "Change % -3.08%" in text  # negative keeps its sign, no forced +
    assert "Next earnings 2026-11-17" in text  # string fields pass through


def test_digest_missing_fields_omitted():
    snap = _mk_snap(name="Co", price=5.5)
    text = fb.digest_text("CO", snap, [])
    assert "Price 5.50" in text
    assert "P/E" not in text and "Div yield" not in text  # missing -> row skipped


def test_digest_news_section_with_sentiment():
    items = [
        {"ts": "2026-10-23T07:19:00-04:00", "source": "benzinga", "title": "Big move",
         "sentiment": 0.42},
        {"ts": "2026-10-22T10:00:00-04:00", "source": "CNBC", "title": "Other", "sentiment": None},
    ]
    snap = _mk_snap(name="Co", price=1.0)
    text = fb.digest_text("CO", snap, items)
    assert "**📰 Latest news**" in text
    assert "▲+0.42" in text and "Big move" in text
    assert text.count("•") == 2
    assert fb.digest_text("CO", snap, items[:1]).count("•") == 1
    assert fb.digest_text("CO", snap, []) == fb.digest_text("CO", snap, [])


def test_split_discord_respects_limit():
    text = "\n".join(f"line {i:03d} " + "x" * 40 for i in range(80))
    chunks = fb._split_discord(text, limit=950)
    assert all(len(c) <= 950 for c in chunks)
    assert sum(len(c.splitlines()) for c in chunks) == 80  # no line lost
    assert fb._split_discord("hello") == ["hello"]


def test_digest_state_roundtrip(monkeypatch, tmp_path):
    fb.funda_client._atomic_write(fb.DIGEST_STATE_PATH, '{"last_date": "2099-01-01"}')
    assert fb.DIGEST_STATE_PATH.exists()
    import json
    assert json.loads(fb.DIGEST_STATE_PATH.read_text(encoding="utf-8"))["last_date"] == "2099-01-01"
