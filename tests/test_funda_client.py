"""funda_client: quota ledger, field merge priority, SEC XBRL parsing, orchestration."""
import json
from datetime import datetime, timedelta

import pytest

import config
import funda_client as fdc


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
    monkeypatch.setattr(fdc, "_CIK_CACHE", None)
    monkeypatch.setattr(fdc, "_CIK_CACHE_MTIME", None)
    monkeypatch.setattr(fdc, "_MINUTE_COUNTS", {})
    # keep tests offline by default: no keys
    for name in ("FMP_API_KEY", "FINNHUB_API_KEY", "ALPHAVANTAGE_API_KEY",
                 "TWELVEDATA_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


def _edgar_facts_fixture():
    end1 = "2026-03-31"
    end0 = "2025-03-31"
    def q(start, end, val, form="10-Q", filed="2026-05-01"):
        return {"start": start, "end": end, "val": val, "form": form, "filed": filed}
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [
                        q("2025-01-01", end0, 100_000_000.0, filed="2025-04-30"),
                        q("2026-01-01", end1, 120_000_000.0),
                    ]},
                },
                "NetIncomeLoss": {
                    "units": {"USD": [
                        q("2025-01-01", end0, 10_000_000.0, filed="2025-04-30"),
                        q("2026-01-01", end1, 12_000_000.0),
                    ]},
                },
                "GrossProfit": {"units": {"USD": [q("2026-01-01", end1, 60_000_000.0)]}},
                "OperatingIncomeLoss": {"units": {"USD": [q("2026-01-01", end1, 18_000_000.0)]}},
                "Assets": {"units": {"USD": [
                    {"end": "2026-03-31", "val": 500_000_000.0, "form": "10-Q", "filed": "2026-05-01"},
                ]}},
                "Liabilities": {"units": {"USD": [
                    {"end": "2026-03-31", "val": 200_000_000.0, "form": "10-Q", "filed": "2026-05-01"},
                ]}},
                "StockholdersEquity": {"units": {"USD": [
                    {"end": "2026-03-31", "val": 300_000_000.0, "form": "10-Q", "filed": "2026-05-01"},
                ]}},
                "AssetsCurrent": {"units": {"USD": [
                    {"end": "2026-03-31", "val": 200_000_000.0, "form": "10-Q", "filed": "2026-05-01"},
                ]}},
                "LiabilitiesCurrent": {"units": {"USD": [
                    {"end": "2026-03-31", "val": 100_000_000.0, "form": "10-Q", "filed": "2026-05-01"},
                ]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                    q("2025-01-01", end0, 40_000_000.0, filed="2026-04-01"),
                    q("2026-01-01", end1, 44_000_000.0),
                ]}},
                "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
                    q("2026-01-01", end1, -4_000_000.0),
                ]}},
                "EarningsPerShareDiluted": {
                    "units": {"USD/shares": [q("2026-01-01", end1, 1.25)]},
                },
            }
        },
        "entityName": "TESTCO",
    }


def test_quota_ledger_rollover():
    # seed yesterday's state
    yday = (datetime.now(fdc.NY) - timedelta(days=1)).strftime("%Y-%m-%d")
    config.FUNDA_STATE_PATH.write_text(json.dumps(
        {"providers": {"fmp": {"date": yday, "used": fdc.config.FMP_DAILY_CAP}}}
    ), encoding="utf-8")
    assert fdc.quota_status()["fmp"]["used"] == 0
    fdc._quota_use("fmp")
    assert fdc.quota_status()["fmp"] == {"used": 1, "cap": fdc.config.FMP_DAILY_CAP, "ok": True}
    # exhaust the cap
    for _ in range(fdc.config.FMP_DAILY_CAP - 1):
        fdc._quota_use("fmp")
    assert fdc.quota_status()["fmp"]["ok"] is False


def test_first_provider_wins_per_field():
    snap = fdc._new_snapshot("AAPL", "stock")
    fdc._set_field(snap, "name", "Apple Inc.", "sec_edgar")
    fdc._set_field(snap, "name", "AAPL profile name", "fmp")
    assert snap["fields"]["name"] == {"v": "Apple Inc.", "src": "sec_edgar", "ts": snap["updated"]}
    fdc._set_field(snap, "pe", None, "fmp")
    assert "pe" not in snap["fields"]
    fdc._set_field(snap, "pe", 31.123456789, "fmp")
    assert snap["fields"]["pe"]["v"] == 31.1235


def test_edgar_facts_parsing(monkeypatch):
    monkeypatch.setattr(fdc, "_edgar_cik", lambda symbol: 320193)
    monkeypatch.setattr(fdc, "_get_json", lambda *a, **k: (_edgar_facts_fixture(), None))
    snap = fdc._new_snapshot("AAPL", "stock")
    assert fdc._edgar_fill(snap, "AAPL") is True
    f = snap["fields"]
    assert f["name"]["src"] == "sec_edgar"
    assert f["revenue_growth_yoy"]["v"] == pytest.approx(20.0)
    assert f["gross_margin"]["v"] == pytest.approx(50.0)
    assert f["operating_margin"]["v"] == pytest.approx(15.0)
    assert f["net_margin"]["v"] == pytest.approx(10.0)
    assert f["de_ratio"]["v"] == pytest.approx(round(200_000_000.0 / 300_000_000.0, 4))
    assert f["current_ratio"]["v"] == pytest.approx(2.0)
    # TTM needs 4 consecutive quarters; the fixture has 2 rows, so SEC leaves it
    # to the next provider instead of reporting a partial (or averaged) figure
    assert "revenue_ttm" not in f
    assert snap["quarters"][-1]["revenue_yoy_pct"] == pytest.approx(20.0)
    # FCF = op cash flow (+ capex which is stored negative on EDGAR)
    assert f["fcf_ttm"]["v"] == pytest.approx(44_000_000.0 + (-4_000_000.0))
    assert len(snap["quarters"]) == 2
    assert snap["quarters"][-1]["end"] == "2026-03-31"
    assert snap["quarters"][-1]["revenue"] == 120_000_000.0


def test_same_quarter_prior():
    rows = [
        {"end": "2025-03-31", "val": 100.0, "form": "10-Q", "filed": "20250430"},
        {"end": "2025-12-31", "val": 150.0, "form": "10-K", "filed": "20260201"},
        {"end": "2026-03-31", "val": 120.0, "form": "10-Q", "filed": "20260430"},
    ]
    prior = fdc._same_quarter_prior(rows, rows[-1])
    assert prior is not None and prior["end"] == "2025-03-31"


def test_refresh_symbol_skips_fresh_snapshot(monkeypatch):
    snap = fdc._new_snapshot("MSFT", "stock")
    snap["fields"]["name"] = {"v": "Microsoft", "src": "fmp", "ts": snap["updated"]}
    snap["updated"] = datetime.now(fdc.NY).isoformat(timespec="seconds")
    config.FUNDA_DIR.joinpath("MSFT.json").write_text(json.dumps(snap), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("network called on fresh snapshot")
    monkeypatch.setattr(fdc, "_get_json", boom)
    monkeypatch.setattr(fdc, "_fh_fill", boom)
    monkeypatch.setattr(fdc, "_yf_fill", boom)
    out = fdc.refresh_symbol("MSFT")
    assert out is not None and out["fields"]["name"]["v"] == "Microsoft"


def test_refresh_fallback_chain_fmp_then_skips(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "K1")

    # EDGAR fails (symbol not in SEC list), FMP returns profile + metrics
    monkeypatch.setattr(fdc, "_edgar_cik", lambda symbol: None)

    def fake_get_json(url, *, params=None, headers=None, per_minute=None):
        if "stable/profile" in url:
            return [{
                "companyName": "Testco Inc", "sector": "Tech", "industry": "Semi",
                "currency": "USD", "marketCap": 1_000_000_000, "beta": 1.2,
                "range": "10-20", "volAvg": 5_000_000, "lastDiv": 0.5,
                "price": 15.0, "exchangeShortName": "NASDAQ",
                "description": "d" * 30,
            }], None
        if "key-metrics-ttm" in url:
            return [{"peRatioTTM": 30.0, "epsTTM": 0.5, "roeTTM": 11.0}], None
        return None, "unmapped-url"
    monkeypatch.setattr(fdc, "_get_json", fake_get_json)

    snap = fdc.refresh_symbol("TEST", force=True)
    f = snap["fields"]
    assert f["name"]["src"] == "fmp"
    assert f["pe"]["src"] == "fmp" and f["pe"]["v"] == 30.0
    assert f["week52_high"]["v"] == 20.0 and f["week52_low"]["v"] == 10.0
    assert f["div_yield"]["src"] == "fmp"  # 0.5 / 15.0 * 100
    assert snap["sources_used"] == ["fmp"]
    assert fdc.quota_status()["fmp"]["used"] >= 1


def test_ttm_sum_needs_four_consecutive_quarters():
    rows = [{"end": e, "val": v} for e, v in
            (("2025-06-30", 10.0), ("2025-09-30", 11.0), ("2025-12-31", 12.0), ("2026-03-31", 13.0))]
    assert fdc._ttm_sum(rows) == 46.0
    gap = [rows[0], rows[1], rows[3], {"end": "2026-06-30", "val": 14.0}]   # Q4 only in the 10-K
    assert fdc._ttm_sum(gap) is None
    assert fdc._ttm_sum(rows[:3]) is None
    assert fdc._yoy_pct(120.0, 100.0) == 20.0
    assert fdc._yoy_pct(-50.0, -100.0) == 50.0
    assert fdc._yoy_pct(1.0, 0.0) is None
