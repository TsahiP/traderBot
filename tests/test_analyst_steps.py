import json
from datetime import date

import analyst_steps as st


def _data(**extra):
    return {
        "symbol": "NVDA", "updated": "u", "news": [], "recent_signals": [],
        "snapshot_errors": [], "quarters": [], "fields": {},
        "smc_daily": {"price": 216.0, "last_choch": {"side": "bull", "bar": 458, "price": 227.7},
                      "open_fvgs": [{"lo": 1.5, "hi": 2.5, "bar": 361}]},
        "squeeze_daily": {"color": "pink"}, "trade_plan": None, **extra,
    }


def _plan(**over):
    plan = {
        "ok": True, "entry": 216.0, "stop": 187.0, "stop_basis": "order_block",
        "risk_per_share": 29.0, "atr_atr_multiple": 4.72,
        "tp1": {"price": 243.369995, "share_pct": 50, "rr": 0.94},
        "tp2": {"price": None, "share_pct": 50, "rr": None}, "combined_rr": None,
        "shares": 8, "position_value": 1728.0, "risk_usd_actual": 232.0,
        "flags": ["no_supply_block", "tp1_rr_below_1", "stop_far_atr"],
    }
    plan.update(over)
    return plan


def test_step3_slice_drops_bar_indices():
    sl = st.step_slice(3, _data())
    dumped = json.dumps(sl)
    assert "bar" not in dumped and "227.7" in dumped


def test_clean_fundamentals_drops_stale_sec_block():
    data = _data(
        fields={"gross_margin": {"v": 2323.41, "src": "sec_edgar"},
                "revenue_growth_yoy": {"v": 40.8, "src": "sec_edgar"},
                "pe": {"v": 26.8, "src": "yfinance"}},
        quarters=[{"end": "2020-01-26", "revenue": 1}],
    )
    fields, quarters, gaps = st.clean_fundamentals(data, today=date(2026, 10, 27))
    assert fields == {"pe": 26.8} and quarters == [] and gaps == [st.STALE_NOTE]


def test_clean_fundamentals_keeps_fresh_data():
    data = _data(
        fields={"net_margin": {"v": 30.6, "src": "sec_edgar"}},
        quarters=[{"end": "2026-07-26", "revenue": 1}],
    )
    fields, quarters, gaps = st.clean_fundamentals(data, today=date(2026, 10, 27))
    assert fields == {"net_margin": 30.6} and len(quarters) == 1 and gaps == []


def test_unknown_numbers():
    src = '{"price": 216.0, "pe": 26.8281, "date": "2026-11-17"}'
    assert st.unknown_numbers("המחיר 216.0 ו-PE 26.8281 ב-15M, 3 מתוך 5", src) == []
    assert st.unknown_numbers("יעד 243.37 וסטופ 187.0", src) == ["243.37", "187.0"]


def test_render_execution_exact_numbers_and_no_target_phrase():
    text, flags = st.render_execution(_plan())
    for needle in ("216.00", "187.00", "29.00", "243.37", "0.94", "8 מניות", "232.00"):
        assert needle in text
    assert "null" not in text and "None" not in text
    assert "אין יעד שני" in text
    assert text.splitlines()[-1].startswith("סיכום: התוכנית נושאת דגלי אזהרה")
    assert flags == ["no_supply_block", "tp1_rr_below_1", "stop_far_atr"]


def test_render_execution_clean_plan_and_missing_plan():
    text, _ = st.render_execution(_plan(flags=[], tp2={"price": 260.0, "share_pct": 50, "rr": 2.1},
                                        combined_rr=1.5, tp1={"price": 230.0, "share_pct": 50, "rr": 1.2}))
    assert "260.00" in text and "R:R משולב 1.5" in text
    assert text.splitlines()[-1].startswith("סיכום: תוכנית נקייה")
    text, flags = st.render_execution(None)
    assert "אין תוכנית" in text and flags == []


def test_parse_verdict_caps_go_on_quality_flag():
    v, c, reason = st.parse_verdict("GO - confidence 5/5\nנראה טוב", ["tp1_rr_below_1"])
    assert (v, c) == ("WAIT", 3) and "tp1_rr_below_1" in reason
    assert st.parse_verdict("GO - confidence 4/5\nטוב", ["no_supply_block"]) == ("GO", 4, "טוב")
    assert st.parse_verdict("garbage", [])[:2] == ("WAIT", 2)


def test_build_card_layout_and_step_failure_isolated():
    calls = []

    def call(messages, max_tokens):
        calls.append(messages)
        if len(calls) == 2:
            raise RuntimeError("model down")
        if "DIGEST" in messages[1]["content"]:
            return "WAIT - confidence 3/5\nסיבה"
        return "גוף\nסיכום: ok"

    card = st.build_card(_data(trade_plan=_plan()), call)
    titles = [ln for ln in card.splitlines() if ln.startswith("## ")]
    assert titles == ["## Step 1 — Catalysts", "## Step 2 — Macro", "## Step 3 — Smart Money",
                      "## Step 4 — Squeeze Momentum", "## Step 5 — Execution plan", "## Verdict"]
    assert "לא זמין" in card.split("## Step 3")[0].split("## Step 2")[1]   # step 2 failed
    assert "WAIT - confidence 3/5" in card


def test_ask_retries_once_on_invented_numbers():
    outputs = iter(["המחיר 999.5", "המחיר 216.0"])
    seen = []

    def call(messages, max_tokens):
        seen.append(messages[1]["content"])
        return next(outputs)

    out = st._ask(call, "sys", "DATA", None, '{"price": 216.0}', 100)
    assert out == "המחיר 216.0" and "999.5" in seen[1]


def test_leaked_keys_detected_and_retried():
    assert st.leaked_keys("ה-volume_ratio עומד על 0.86 ו-forward_pe") == ["volume_ratio", "forward_pe"]
    assert st.leaked_keys("EMA 200, 15M, R:R") == []
    outputs = iter(["ה-volume_ratio נמוך", "הנפח נמוך"])
    seen = []

    def call(messages, max_tokens):
        seen.append(messages[1]["content"])
        return next(outputs)

    assert st._ask(call, "sys", "DATA", None, "{}", 100) == "הנפח נמוך"
    assert "volume_ratio" in seen[1]


def test_render_ratio_plan():
    plan = {
        "ok": True, "mode": "ratio", "target_rr": 3.0, "invest_usd": 2000.0, "entry": 100.0,
        "stop": 92.0, "stop_basis": "ratio", "risk_per_share": 8.0, "atr_atr_multiple": 4.0,
        "tp1": {"price": 124.0, "share_pct": 100, "rr": 3.0}, "tp2": {"price": None},
        "shares": 20, "position_value": 2000.0, "risk_usd_actual": 160.0,
        "potential_profit_usd": 480.0, "flags": [],
    }
    text, flags = st.render_execution(plan)
    for needle in ("1:3", "2000.00$", "20 מניות", "124.00", "92.00", "160.00$", "480.00$"):
        assert needle in text
    assert "null" not in text and "breakeven" not in text and "אין יעד שני" not in text
    assert text.splitlines()[-1].startswith("סיכום: תוכנית 1:3 נקייה")
    bad, _ = st.render_execution({**plan, "flags": ["stop_tight_atr"]})
    assert "דגלי אזהרה" in bad and bad.splitlines()[-1].startswith("סיכום: התוכנית נושאת דגלי אזהרה")
