import base64
import json

import pandas as pd

import analyst_context as ac


def _bars(n=260, tf="d"):
    idx = pd.date_range("2026-03-02", periods=n, freq="D" if tf == "d" else "15min")
    rows = []
    for i in range(n):
        c = 100.0 + 0.05 * i + 4.0 * ((i % 40) / 40.0)
        o = c - 0.2
        rows.append({"Open": o, "High": c + 0.6, "Low": c - 0.6, "Close": c, "Volume": 1000.0 + i})
    return pd.DataFrame(rows, index=idx)


def _snap(symbol="NVDA", updated="2026-10-23T12:00:00-04:00"):
    return {
        "symbol": symbol,
        "asset": "stock",
        "updated": updated,
        "fields": {
            "price": {"v": 212.2, "src": "fmp", "ts": "t"},
            "pe": {"v": 26.8, "src": "fmp", "ts": "t"},
        },
        "quarters": [{"end": "2026-07-31", "revenue": 1, "eps": 2, "src": "sec"}],
        "errors": ["finnhub metrics: gated"],
        "sources_used": [],
    }


def _offline(monkeypatch, tmp_path, daily=None, intraday=None):
    funda_bank = tmp_path / "funda"
    funda_bank.mkdir(exist_ok=True)
    monkeypatch.setattr(ac.config, "FUNDA_DIR", funda_bank)
    (funda_bank / "NVDA.json").write_text(json.dumps(_snap()), encoding="utf-8")
    monkeypatch.setattr(ac.funda_client, "refresh_symbol", lambda s, force=False: _snap())
    monkeypatch.setattr(ac.news_client, "poll_symbol", lambda s, company=None: ([], []))
    monkeypatch.setattr(
        ac.news_client,
        "read_symbol_news",
        lambda s: [
            {"id": "a", "ts": "2026-10-23T07:00:00-04:00", "source": "benzinga",
             "title": "T" * 200, "summary": None, "sentiment": 0.1}
        ],
    )
    monkeypatch.setattr(
        ac.signals,
        "read_signals",
        lambda limit=200: [
            {"symbol": "NVDA", "ts": "2026-10-23T06:00:00-04:00", "timeframe": "1d",
             "direction": "long", "label": "macd row", "entry": 1.0, "stop": 0.9,
             "target": 1.3, "close": 1.05, "key": "k", "filtered": False},
            {"symbol": "SPY", "ts": "2026-10-22T06:00:00-04:00", "filtered": False},
        ][:limit],
    )
    fetched: list[str] = []

    def fake_bars(symbol, tf, period):
        fetched.append(tf)
        return {"1d": daily, "15m": intraday}[tf]

    monkeypatch.setattr(ac.market_data, "fetch_ohlcv_period", fake_bars)
    monkeypatch.setattr(ac.market_context, "market_context", lambda *a, **k: {
        "qqq": {"ok": True, "trend": {"above_ema_200": True}},
        "vix": {"ok": True, "daily_pct": -4.2},
        "updated": "2026-10-24T10:00:00-04:00",
    })
    monkeypatch.setattr(ac, "_ex_dividend_date", lambda s: "2026-11-05")
    return fetched


def test_gather_offline(monkeypatch, tmp_path):
    _offline(monkeypatch, tmp_path)
    data = ac.gather_symbol_data(
        "NVDA",
        watch_cfg={"lists": [{"name": "Core", "symbols": ["NVDA", "SPY"]}]},
    )
    assert data["asset"] == "stock" and data["fields"]["price"]["src"] == "fmp"
    assert len(data["news"]) == 1 and len(data["news"][0]["title"]) <= 140
    assert data["recent_signals"][0]["label"] == "macd row"
    assert data["watchlists"] == ["Core"]
    assert data["snapshot_errors"] == ["finnhub metrics: gated"]
    assert data["macro"]["qqq"]["ok"] is True
    assert "no daily bars" in (data["price_action"] or {}).get("error", "")
    assert data["ex_dividend"] == "2026-11-05"


def test_gather_full_steps(monkeypatch, tmp_path):
    daily, intraday = _bars(260, "d"), _bars(1400, "15m")
    _offline(monkeypatch, tmp_path, daily=daily, intraday=intraday)
    data = ac.gather_symbol_data("NVDA")

    pa = data["price_action"]
    assert pa["ok"] is True
    assert isinstance(pa["change_pct"], float)
    assert data["smc_daily"]["ok"] is True
    assert data["smc_15m"]["ok"] is True
    assert data["squeeze_daily"]["ok"] is True and "squeeze" in data["squeeze_daily"]
    assert data["squeeze_15m"]["ok"] is True
    assert data["atr_daily"] and data["atr_daily"] > 0
    plan = data["trade_plan"]
    assert plan["ok"] is True and plan["stop"] < plan["entry"]
    assert plan["tp1"]["price"] is not None
    assert sorted(data["charts"]) == ["15m", "1d"]
    for png in data["charts"].values():
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_gather_bad_bars_records_errors(monkeypatch, tmp_path):
    def boom_bars(symbol, tf, period):
        if tf == "1d":
            raise RuntimeError("yf stuck")
        return None

    _offline(monkeypatch, tmp_path)
    monkeypatch.setattr(ac.market_data, "fetch_ohlcv_period", boom_bars)
    data = ac.gather_symbol_data("NVDA")
    assert any("1d bars: yf stuck" in e for e in data["fetch_errors"])
    assert data.get("trade_plan") is None
    assert data["charts"] == {}


def test_gather_absorbs_refresh_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(ac.config, "FUNDA_DIR", tmp_path / "funda")
    monkeypatch.setattr(
        ac.funda_client, "refresh_symbol",
        lambda s, force=False: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(ac.news_client, "poll_symbol", lambda s, company=None: ([], []))
    monkeypatch.setattr(ac.news_client, "read_symbol_news", lambda s: [])
    monkeypatch.setattr(ac.signals, "read_signals", lambda limit=200: [])
    monkeypatch.setattr(ac.market_data, "fetch_ohlcv_period", lambda *a: None)
    monkeypatch.setattr(ac.market_context, "market_context", lambda *a, **k: {"updated": "u"})
    monkeypatch.setattr(ac, "_ex_dividend_date", lambda s: None)
    data = ac.gather_symbol_data("XX")
    assert "boom" in "".join(data["fetch_errors"])
    assert data["price_action"]["ok"] is False


def test_build_context_json_shape():
    data = {
        "symbol": "A", "asset": "stock", "updated": None, "fields": {},
        "quarters": [], "news": [], "recent_signals": [], "watchlists": [],
        "snapshot_errors": [], "fetch_errors": ["boom"],
        "macro": {"qqq": {"ok": True}},
        "smc_daily": {"ok": True}, "smc_15m": None,
        "squeeze_daily": {"ok": True}, "squeeze_15m": None,
        "trade_plan": {"ok": True}, "holding": {"qty": 5}, "price_action": {"ok": True},
        "ex_dividend": "2026-11-01",
    }
    parsed = json.loads(ac.build_context_json(data))
    assert parsed["symbol"] == "A"
    assert parsed["data_problem_notes"] == ["boom"]
    for step in ("step1_catalysts", "step2_macro", "step3_smc", "step4_squeeze", "step5_execution"):
        assert step in parsed
    assert parsed["step2_macro"]["qqq"]["ok"] is True
    assert parsed["step5_execution"]["ok"] is True


def test_analysis_messages_shape():
    data = {
        "symbol": "AAPL", "asset": "stock", "updated": "u", "fields": {"pe": {"v": 1, "src": "s"}},
        "quarters": [], "news": [], "recent_signals": [], "watchlists": [],
        "snapshot_errors": [], "fetch_errors": [],
    }
    msgs = ac.analysis_messages(data)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert isinstance(msgs[1]["content"], str)
    assert "AAPL" in msgs[1]["content"]
    assert '"pe"' in msgs[1]["content"]
    assert "Verdict" in msgs[0]["content"]
    assert '"WAIT - confidence X/5"' in msgs[0]["content"]


def test_analysis_messages_with_images():
    data = {"symbol": "AAPL", "updated": "u", "fields": {}, "quarters": [],
            "news": [], "recent_signals": [], "watchlists": [],
            "snapshot_errors": [], "fetch_errors": []}
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    msgs = ac.analysis_messages(data, [("1d chart", png)])
    content = msgs[1]["content"]
    assert isinstance(content, list)
    b64 = base64.b64encode(png).decode("ascii")
    assert {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}} in content
    assert any(p["type"] == "text" and "1d chart" in p["text"] for p in content)


def test_run_analysis_vision_and_fallback(monkeypatch, tmp_path):
    data = {"symbol": "AAPL", "updated": "u", "fields": {}, "quarters": [],
            "news": [], "recent_signals": [], "watchlists": [],
            "snapshot_errors": [], "fetch_errors": [],
            "charts": {"1d": b"\x89PNG\r\n\x1a\nxxxx", "15m": b"\x89PNG\r\n\x1a\nyyyy"}}
    monkeypatch.setattr(ac.config, "ANALYST_VISION", True)

    calls: list = []

    def fake_llm(messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            raise RuntimeError("server rejected images")
        return "## Step 1 — Catalysts\nok", {"finish_reason": "stop"}

    monkeypatch.setattr(ac, "llm_chat_ex", fake_llm)
    text, model = ac.run_analysis(data)
    assert text.startswith("## Step 1")
    assert model or None is not None
    assert len(calls) == 2
    assert isinstance(calls[0][1]["content"], list)   # first try: with images
    assert isinstance(calls[1][1]["content"], str)    # fallback: text only


def test_run_analysis_text_only_when_vision_off(monkeypatch):
    data = {"symbol": "AAPL", "updated": "u", "fields": {}, "quarters": [],
            "news": [], "recent_signals": [], "watchlists": [],
            "snapshot_errors": [], "fetch_errors": [],
            "charts": {"1d": b"\x89PNG\r\n\x1a\nxxxx"}}
    monkeypatch.setattr(ac.config, "ANALYST_VISION", False)
    calls: list = []

    def fake_llm(messages, **kw):
        calls.append(messages)
        return "## Verdict\nGO - confidence 4/5", {"finish_reason": "stop"}

    monkeypatch.setattr(ac, "llm_chat_ex", fake_llm)
    text, _model = ac.run_analysis(data)
    assert "GO" in text
    assert len(calls) == 1
    assert isinstance(calls[0][1]["content"], str)   # no image parts


def test_analysis_call_escalates_budget_on_empty(monkeypatch):
    calls: list[int] = []

    def fake_llm(messages, max_tokens, **kw):
        calls.append(max_tokens)
        if len(calls) == 1:
            return "", {"finish_reason": "length", "reasoning_tokens": 5999}
        return "## Verdict\nWAIT - confidence 2/5", {"finish_reason": "stop", "reasoning_tokens": 800}

    monkeypatch.setattr(ac, "llm_chat_ex", fake_llm)
    text = ac._analysis_call([{"role": "user", "content": "x"}])
    assert text.startswith("## Verdict")
    assert calls[:2] == [12000, 27000]  # 12000 -> 12000*2+3000


def test_analysis_call_escalates_on_truncated_answer(monkeypatch):
    calls: list[int] = []

    def fake_llm(messages, max_tokens, **kw):
        calls.append(max_tokens)
        if len(calls) == 1:  # partial answer cut by the budget
            return "## Step 3 — Smart Money\n- 15M: price 172", {"finish_reason": "length", "reasoning_tokens": 5000}
        return "## Step 1 — Catalysts\n## Verdict\nWAIT - confidence 3/5", {"finish_reason": "stop", "reasoning_tokens": 900}

    monkeypatch.setattr(ac, "llm_chat_ex", fake_llm)
    text = ac._analysis_call([{"role": "user", "content": "x"}])
    assert "## Verdict" in text
    assert calls[1] == 27000


def test_price_action_read():
    daily = _bars(60)
    pa = ac._price_action_daily(daily)
    assert pa["ok"] is True
    assert pa["read"] in (None, "absorption", "distribution")
    # big dump volume near lows -> distribution
    bad = _bars(60).copy()
    bad.iloc[-1, bad.columns.get_loc("Close")] = bad["Low"].iloc[-1] + 0.05
    bad.iloc[-1, bad.columns.get_loc("Volume")] = bad["Volume"].mean() * 5
    assert ac._price_action_daily(bad)["read"] == "distribution"


def test_atr():
    df = _bars(40)
    atr = ac._atr(df)
    assert atr and atr > 0
    assert ac._atr(_bars(5)) is None


def test_format_reply_chunks_and_footer():
    data = {"symbol": "AAPL", "updated": "2026-10-23T12:00:00-04:00", "asset": "stock"}
    short = "## Verdict\nGO - confidence 4/5"
    out = ac.format_reply("AAPL", short, data)
    assert len(out) == 1
    assert out[0].startswith("**🔎 פונדי אנליסט — AAPL**")
    assert "## Verdict" in out[0]
    assert "סנפשוט 2026-10-23T12:00" in out[0] and "שע'" in out[0]

    analysis = "\n".join(f"line {i:03d} " + "x" * 60 for i in range(60))
    chunks = ac.format_reply("AAPL", analysis, data)
    assert all(len(c) <= 2000 for c in chunks)
    assert "שע'_" in chunks[-1]  # footer rides the last chunk


def test_split_discord_basic():
    assert ac.split_discord("hello") == ["hello"]
