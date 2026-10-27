"""Fundamentals: multi-provider free-tier fetcher with field-level fallback.

Providers (best -> worst per category, all free tiers):
  sec_edgar     no key   authoritative XBRL statements for US stocks (10-Q/10-K)
  fmp           FMP_API_KEY          ~250/day: profile, key metrics, statements
  finnhub       FINNHUB_API_KEY      60/min: metrics (PE, margins, 52w), earnings calendar
  yfinance      no key               price/market-cap snapshot (unofficial: keep light)
  twelvedata    TWELVEDATA_API_KEY   800 credits/day: price fallback
  alphavantage  ALPHAVANTAGE_API_KEY 25/day: OVERVIEW fallback

Snapshots:  output/funda/<symbol>.json
State:      output/funda_state.json  (per-provider daily quota ledger)

Every snapshot field records which provider supplied it ({name: {v, src, ts}}),
so five providers that disagree are still auditable. Missing keys just remove a
provider from the chain — the feature degrades, it never crashes.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

NY = ZoneInfo("America/New_York")
_STATE_LOCK = threading.Lock()

# Seconds/minute tracking for per-minute rate limited providers (AV 5/min,
# Finnhub 60/min practical slice). In-memory only; daily caps are persistent.
_MINUTE_LOCK = threading.Lock()
_MINUTE_COUNTS: dict[str, tuple[str, int]] = {}

USER_AGENT = "tradebot research admin@example.com"
HTTP_TIMEOUT = 25


# ------------------------------------------------------------------ state

def _today_ny() -> str:
    return datetime.now(NY).strftime("%Y-%m-%d")


def _atomic_write(path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_state() -> dict:
    if config.FUNDA_STATE_PATH.exists():
        try:
            raw = json.loads(config.FUNDA_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (json.JSONDecodeError, OSError):
            pass
    return {"providers": {}, "passes": {}}


def _save_state(state: dict) -> None:
    _atomic_write(config.FUNDA_STATE_PATH, json.dumps(state, indent=2))


def quota_status() -> dict:
    state = _load_state()
    today = _today_ny()
    out = {}
    for name, cap in (
        ("fmp", config.FMP_DAILY_CAP),
        ("alphavantage", config.ALPHAVANTAGE_DAILY_CAP),
        ("twelvedata", config.TWELVEDATA_DAILY_CAP),
        ("finnhub", config.FINNHUB_DAILY_SOFT_CAP),
        ("sec_edgar", None),
        ("yfinance", None),
    ):
        used = 0
        prov = state.get("providers", {}).get(name, {})
        if isinstance(prov, dict) and prov.get("date") == today:
            used = int(prov.get("used", 0) or 0)
        out[name] = {"used": used, "cap": cap, "ok": cap is None or used < cap}
    return out


def _quota_ok(provider: str) -> bool:
    return quota_status().get(provider, {"ok": False})["ok"]


def _quota_use(provider: str) -> None:
    with _STATE_LOCK:
        state = _load_state()
        today = _today_ny()
        prov = state.setdefault("providers", {}).setdefault(provider, {})
        if prov.get("date") != today:
            prov["date"], prov["used"] = today, 0
        prov["used"] = int(prov.get("used", 0) or 0) + 1
        _save_state(state)


def _minute_ok(provider: str, per_minute: int) -> bool:
    now_key = datetime.now(NY).strftime("%Y-%m-%d %H:%M")
    with _MINUTE_LOCK:
        key, count = _MINUTE_COUNTS.get(provider, (now_key, 0))
        if key != now_key:
            key, count = now_key, 0
        count += 1
        _MINUTE_COUNTS[provider] = (key, count)
        return count <= per_minute


# ------------------------------------------------------------------ http

def _get_text(url: str, *, params: dict | None = None):
    """GET that returns raw text — for RSS endpoints, (body_str, err|None)."""
    import requests

    try:
        r = requests.get(url, params=params or {}, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        return r.text, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:120]


def _get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
              per_minute: int | None = None):
    import requests

    if per_minute is not None and not _minute_ok("http_" + (url.split("/")[2] if "://" in url else url), per_minute):
        return None, "minute-budget"
    try:
        r = requests.get(url, params=params or {}, timeout=HTTP_TIMEOUT,
                         headers={"User-Agent": USER_AGENT, **(headers or {})})
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        data = r.json()
        if isinstance(data, dict) and data.get("Error Message"):
            return None, str(data["Error Message"])[:120]
        if isinstance(data, dict) and data.get("error"):
            return None, str(data["error"])[:120]
        return data, None
    except ValueError:
        return None, "non-json body (html — bad key or geo-blocked?)"


# Alpha Vantage key is commonly written either way — accept both spellings.
_AV_ENV_KEYS = ("ALPHAVANTAGE_API_KEY", "ALPHA_VANTAGE_API_KEY")


def av_key() -> str | None:
    for name in _AV_ENV_KEYS:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return None


def configured_keys() -> dict[str, bool]:
    return {
        "fmp": bool(os.getenv("FMP_API_KEY")),
        "finnhub": bool(os.getenv("FINNHUB_API_KEY")),
        "alphavantage": bool(av_key()),
        "twelvedata": bool(os.getenv("TWELVEDATA_API_KEY")),
        "sec_edgar": True,
        "yfinance": True,
    }


# ------------------------------------------------------------------ snapshots

def _snapshot_path(symbol: str):
    return config.FUNDA_DIR / f"{symbol.replace('=', '_').replace(':', '_')}.json"


def _new_snapshot(symbol: str, asset: str) -> dict:
    return {
        "symbol": symbol,
        "asset": asset,
        "updated": datetime.now(NY).isoformat(timespec="seconds"),
        "fields": {},
        "quarters": [],
        "sources_used": [],
        "errors": [],
    }


def read_snapshot(symbol: str) -> dict | None:
    path = _snapshot_path(symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def snapshot_age_hours(snapshot: dict | None) -> float | None:
    if not snapshot or not snapshot.get("updated"):
        return None
    try:
        ts = datetime.fromisoformat(snapshot["updated"])
        return max(0.0, (datetime.now(ts.tzinfo) - ts).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return None


def is_fresh(snapshot: dict | None) -> bool:
    age = snapshot_age_hours(snapshot)
    return age is not None and age < float(config.FUNDA_TTL_HOURS)


# ------------------------------------------------------------------ set field

def _set_field(snap: dict, name: str, value, source: str) -> None:
    if value is None:
        return
    if isinstance(value, float):
        value = round(value, 4)
        if abs(value) >= 1e13:
            value = None
            return
    if value == "":
        return
    if name not in snap["fields"]:
        snap["fields"][name] = {"v": value, "src": source, "ts": snap["updated"]}


# ------------------------------------------------------------------ sec edgar

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CIK_CACHE: dict[str, int] | None = None
_CIK_CACHE_MTIME: float | None = None


def _cik_map() -> dict[str, int]:
    global _CIK_CACHE, _CIK_CACHE_MTIME
    path = config.FUNDA_DIR / "sec_ciks.json"
    if path.exists():
        mtime = path.stat().st_mtime
        if _CIK_CACHE is not None and mtime == _CIK_CACHE_MTIME:
            return _CIK_CACHE
        try:
            _CIK_CACHE = {
                k.upper(): int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()
            }
            _CIK_CACHE_MTIME = mtime
            return _CIK_CACHE
        except Exception:  # noqa: BLE001 — rebuild below
            pass
    data, err = _get_json(SEC_TICKERS_URL, per_minute=None)
    mapping: dict[str, int] = {}
    if not err and isinstance(data, dict):
        for row in data.values():
            tkr = str(row.get("ticker", "")).upper()
            cik = int(row.get("cik_str", 0) or 0)
            if tkr and cik and tkr not in mapping:
                mapping[tkr] = cik
        try:
            _atomic_write(path, json.dumps(mapping, indent=1))
            _CIK_CACHE = mapping
            _CIK_CACHE_MTIME = path.stat().st_mtime
        except OSError:
            pass
    return mapping if _CIK_CACHE is None else _CIK_CACHE


def _edgar_cik(symbol: str) -> int | None:
    return _cik_map().get(symbol.upper())


def _tag_series(us_gaap: dict, names: list[str], unit_hint: str = "USD") -> tuple[str, list] | None:
    for name in names:
        entry = us_gaap.get(name)
        if not isinstance(entry, dict):
            continue
        units = entry.get("units", {})
        units_list = None
        for key in units:
            if unit_hint in key:
                units_list = units[key]
                break
        if not units_list:
            units_list = next(iter(units.values()), None)
        if units_list:
            return name, units_list
    return None


def _dedupe_periods(units_list: list) -> list[dict]:
    """One entry per period end: keep the most recently filed."""
    best: dict[tuple, dict] = {}
    for row in units_list:
        form = str(row.get("form", ""))
        if form not in ("10-K", "10-Q", "8-K"):
            continue
        try:
            filed = int(str(row.get("filed", "0"))[:8].replace("-", "") or 0)
        except ValueError:
            filed = 0
        end = row.get("end")
        start = row.get("start")
        if not end:
            continue
        days = (
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
            if start and end
            else 0
        )
        if days and days > 110:  # annual-ish period
            continue
        key = (end, row.get("fp") or "", row.get("fy") if days == 0 else None)
        prev = best.get(key)
        if prev is None or filed >= prev["_filed"]:
            row["_filed"] = filed
            best[key] = row
    return sorted(best.values(), key=lambda r: r["end"])


def _quarter_rows(us_gaap: dict, names: list[str], unit_hint: str = "USD") -> list[dict]:
    found = _tag_series(us_gaap, names, unit_hint)
    if not found:
        return []
    return _dedupe_periods(found[1])


def _same_quarter_prior(rows: list[dict], target: dict) -> dict | None:
    """Quarter ended ~1 year before target's end."""
    try:
        target_end = datetime.fromisoformat(str(target["end"]))
        t = target_end - timedelta(days=364)
    except ValueError:
        return None
    for row in reversed(rows):
        try:
            end = datetime.fromisoformat(str(row["end"]))
        except ValueError:
            continue
        if abs((end - t).days) <= 75:
            return row
    return None


def _edgar_fill(snap: dict, symbol: str) -> bool:
    """Authoritative statements + derived fields from SEC XBRL company facts."""
    cik = _edgar_cik(symbol)
    if not cik:
        return False
    data, err = _get_json(SEC_COMPANYFACTS_URL.format(cik=cik), per_minute=None)
    if err or not isinstance(data, dict):
        snap["errors"].append(f"sec_edgar: {err or 'bad payload'}")
        return False
    us = (data.get("facts", {}) or {}).get("us-gaap", {}) or {}
    if not isinstance(us, dict):
        return False

    rev_rows = _quarter_rows(us, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues", "SalesRevenueNet",
    ])
    ni_rows = _quarter_rows(us, ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"])
    gp_rows = _quarter_rows(us, ["GrossProfit"])
    oi_rows = _quarter_rows(us, ["OperatingIncomeLoss"])
    eps_rows = _quarter_rows(us, ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"], "USD/shares")
    ocf_rows = _quarter_rows(us, ["NetCashProvidedByUsedInOperatingActivities"])
    capex_rows = _quarter_rows(us, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ])

    def val(rows, idx=-1):
        return rows[idx]["val"] if rows else None

    _instant = lambda names: _quarter_rows(us, names)  # noqa: E731
    assets = _instant(["Assets"])
    liab = _instant(["Liabilities"])
    equity = _instant([
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ])
    assets_now, liabilities_now = val(assets), val(liab)

    rev_q, ni_q = val(rev_rows), val(ni_rows)
    if rev_q:
        gp_q = val(gp_rows)
        oi_q = val(oi_rows)
        if gp_q is not None and rev_q:
            _set_field(snap, "gross_margin", gp_q / rev_q * 100.0, "sec_edgar")
        if oi_q is not None and rev_q:
            _set_field(snap, "operating_margin", oi_q / rev_q * 100.0, "sec_edgar")
        if ni_q is not None and rev_q:
            _set_field(snap, "net_margin", ni_q / rev_q * 100.0, "sec_edgar")
        prior = _same_quarter_prior(rev_rows, rev_rows[-1])
        if prior and float(prior["val"]) > 0:
            growth = (float(rev_q) - float(prior["val"])) / abs(float(prior["val"])) * 100.0
            _set_field(snap, "revenue_growth_yoy", growth, "sec_edgar")

    if assets_now and liabilities_now is not None and val(equity):
        _set_field(snap, "de_ratio", liabilities_now / abs(val(equity)), "sec_edgar")
    current_assets = _quarter_rows(us, ["AssetsCurrent"])
    current_liab = _quarter_rows(us, ["LiabilitiesCurrent"])
    if val(current_assets) and val(current_liab):
        _set_field(snap, "current_ratio", val(current_assets) / val(current_liab), "sec_edgar")

    last_4: dict[str, list] = {}
    for tag, rows in (("revenue_ttm", rev_rows), ("net_income_ttm", ni_rows), ("eps_ttm", eps_rows)):
        if len(rows) >= 2:
            last_4[tag] = [float(r["val"]) for r in rows[-4:]]
            _set_field(snap, tag, sum(last_4[tag]) / len(last_4[tag]), "sec_edgar")
    if len(ocf_rows) >= 1:
        ocf = sum(float(r['val']) for r in ocf_rows[-4:]) if len(ocf_rows) >= 4 else float(ocf_rows[-1]['val'])
        capex = 0.0
        if capex_rows:
            capex = (
                sum(float(r['val']) for r in capex_rows[-4:])
                if len(capex_rows) >= 4
                else float(capex_rows[-1]['val'])
            )
        _set_field(snap, "fcf_ttm", ocf + capex if capex <= 0 else ocf - capex, "sec_edgar")

    # Quarters table (last 5, aligned by end date across tags)
    ends = [r["end"] for r in rev_rows or ni_rows] if (rev_rows or ni_rows) else []
    quarters = []
    for end in ends[-5:]:
        q = {"end": end, "src": "sec_edgar"}
        for name, rows in (("revenue", rev_rows), ("net_income", ni_rows),
                           ("gross_profit", gp_rows), ("eps", eps_rows)):
            for row in rows:
                if row["end"] == end:
                    q[name] = row["val"]
                    break
        if quarters and quarters[-1]["end"] == end:
            continue
        quarters.append(q)
    snap["quarters"] = quarters

    entity = data.get("entityName")
    if entity:
        _set_field(snap, "name", str(entity).title() if entity.isupper() else entity, "sec_edgar")
    return True


# ------------------------------------------------------------------ fmp

def _fmp_get(path: str, key: str, **params):
    data, err = _get_json(
        f"https://financialmodelingprep.com/stable/{path}",
        params={"apikey": key, **params},
    )
    if err:
        return None, err
    if not isinstance(data, list):
        return None, "unexpected payload"
    if data:  # every successful call eats from the shared free pool
        _quota_use("fmp")
    return data, None


def _fmp_first(data) -> dict | None:
    """Profile rows: {symbol,...}; tolerate list or error payload."""
    if not data:
        return None
    return data[0] if isinstance(data, list) and isinstance(data[0], dict) else None


def _num(x):
    try:
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _fmp_fill(snap: dict, symbol: str, key: str) -> bool:
    ok = False
    def use(source, fields):
        nonlocal ok
        for name, val in fields.items():
            if name in snap["fields"]:
                continue
            before = len(snap["fields"])
            _set_field(snap, name, val, source)
            ok = ok or len(snap["fields"]) > before

    profile, err = _fmp_get("profile", key, symbol=symbol)
    if err:
        snap["errors"].append(f"fmp profile: {err}")
    else:
        p = _fmp_first(profile)

        if p:
            last_div = _num(p.get("lastDiv"))
            price_v = _num(p.get("price"))
            use("fmp", {
                "name": p.get("companyName"),
                "sector": p.get("sector") if p.get("sector") != "None" else None,
                "industry": p.get("industry") if p.get("industry") != "None" else None,
                "description": (p.get("description") or "")[:280] or None,
                "exchange": p.get("exchangeShortName"),
                "currency": p.get("currency"),
                "mcap": _num(p.get("marketCap")),
                "beta": _num(p.get("beta")),
                "week52_high": _num((p.get("range") or "").split("-")[-1]),
                "week52_low": _num((p.get("range") or "").split("-")[0]),
                "avg_volume": _num(p.get("volAvg")),
                "div_yield": (
                    (last_div / price_v) * 100.0
                    if last_div is not None and price_v else None
                ),
                "price": price_v,
            })

    metrics, err = _fmp_get("key-metrics-ttm", key, symbol=symbol, limit=1)
    if err:
        snap["errors"].append(f"fmp metrics: {err}")
    else:
        m = _fmp_first(metrics)

        if m:
            use("fmp", {
                "pe": _num(m.get("peRatioTTM") or m.get("pe")),
                "forward_pe": _num(m.get("forwardPE")),
                "peg": _num(m.get("pegTTM") or m.get("peg")),
                "shares_out": _num(m.get("sharesOutstandingTTM") or m.get("marketCapTTM")),
                "div_yield": _num(m.get("dividendYieldTTM")),
                "roe": _num(m.get("roeTTM")),
                "de_ratio": _num(m.get("debtToEquityTTM") or m.get("totalDebtToEquityTTM")),
                "fcf_ttm": _num(m.get("freeCashFlowTTM")),
                "eps_ttm": _num(m.get("epsTTM")),
                "revenue_ttm": _num(m.get("revenueTTM") or m.get("revenuePerShareTTM")),
            })

    is_q, err = _fmp_get("income-statement", key, symbol=symbol, period="quarter", limit=5)
    if err:
        snap["errors"].append(f"fmp income: {err}")
    else:
        quarters = []
        for row in is_q[:5]:
            q = {
                "end": row.get("date"),
                "revenue": _num(row.get("revenue")),
                "net_income": _num(row.get("netIncome")),
                "gross_profit": _num(row.get("grossProfit")),
                "eps": _num(row.get("eps")),
                "src": "fmp",
            }
            quarters.append(q)
        if quarters:
            snap["fmp_quarters"] = quarters
            ok = True
    return ok


# ------------------------------------------------------------------ finnhub

def _fh_get(path: str, key: str, **params):
    data, err = _get_json(
        f"https://finnhub.io/api/v1/{path}",
        params={"token": key, **params},
        per_minute=50,
    )
    if not err and data is not None:
        _quota_use("finnhub")
    return data, err


def _fh_metric(d: dict, *names):
    """Case-insensitive candidate-key lookup inside Finn's metrics payload."""
    for name in names:
        for k, v in d.items():
            if k.lower() == name.lower() and v not in (None, ""):
                return _num(v)
    return None


def _fh_fill(snap: dict, symbol: str, key: str) -> bool:
    ok = False
    metrics, err = _fh_get("stock/metrics", key, symbol=symbol)
    if err:
        snap["errors"].append(f"finnhub metrics: {err}")
    else:
        m = metrics.get("metric") if isinstance(metrics, dict) else None
        if isinstance(m, dict):
            fh_mcap_m = _fh_metric(m, "marketCapitalization")  # returned in millions
            fields = {
                "mcap": fh_mcap_m * 1_000_000 if fh_mcap_m else None,
                "pe": _fh_metric(m, "peTTM", "peBasicExclExtraTTM", "peRatioTTM"),
                "eps_ttm": _fh_metric(m, "epsTTM", "epsInclExtraItemsTTM"),
                "roe": _fh_metric(m, "roeTTM"),
                "de_ratio": _fh_metric(m, "totalDebtTotalEquityAnnual", "totalDebtNetTotalEquityAnnual"),
                "div_yield": _fh_metric(m, "dividendYieldIndicatedAnnual", "dividendYieldAnnual"),
                "beta": _fh_metric(m, "beta"),
                "week52_high": _fh_metric(m, "52WeekHigh"),
                "week52_low": _fh_metric(m, "52WeekLow"),
                "revenue_growth_yoy": _fh_metric(m, "revenueGrowthTTMYoy", "revenueGrowthAnnual"),
                "gross_margin": _fh_metric(m, "grossMarginTTM"),
                "net_margin": _fh_metric(m, "netProfitMarginTTM", "netMarginTTM"),
                "operating_margin": _fh_metric(m, "operatingMarginTTM"),
            }
            fields = {k: v for k, v in fields.items() if v is not None}
            before = len(snap["fields"])
            for name, v in fields.items():
                _set_field(snap, name, v, "finnhub")
            ok = len(snap["fields"]) > before
    prof, err = _fh_get("stock/profile2", key, symbol=symbol)
    if err:
        snap["errors"].append(f"finnhub profile: {err}")
    elif isinstance(prof, list) and prof and isinstance(prof[0], dict):
        p = prof[0]
        for name, v in {
            "name": p.get("name"),
            "sector": p.get("finnhubIndustry"),
            "currency": p.get("currency"),
            "description": (p.get("longDescription") or "")[:280] or None,
            "shares_out": _num(p.get("shareOutstanding")),
        }.items():
            before = len(snap["fields"])
            _set_field(snap, name, v, "finnhub")
            ok = ok or len(snap["fields"]) > before
    return ok


def _fh_earnings(snap: dict, symbol: str, key: str) -> bool:
    """Next earnings date via the (free) earnings calendar."""
    today = datetime.now(NY).date()
    frm = today.isoformat()
    to = (today + timedelta(days=45)).isoformat()
    data, err = _fh_get("calendar/earnings", key, **{"from": frm, "to": to})
    if err or not isinstance(data, dict):
        snap["errors"].append(f"finnhub earnings: {err or 'bad payload'}")
        return False
    rows = data.get("earningsCalendar") or data.get("earnings") or []
    for row in rows:
        if str(row.get("symbol", "")).upper() == symbol.upper():
            _set_field(snap, "next_earnings", row.get("date"), "finnhub")
            if row.get("epsEstimate"):
                _set_field(snap, "next_eps_estimate", _num(row.get("epsEstimate")), "finnhub")
            return True
    return False


# ------------------------------------------------------------------ yfinance

def _yf_fill(snap: dict, symbol: str) -> bool:
    ok = False
    try:
        import yfinance as yf  # lazy: heavy import, only when needed
        ti = yf.Ticker(symbol)
        fi = ti.fast_info
        def fget(name):
            try:
                return getattr(fi, name)
            except (KeyError, AttributeError):
                return None
        price = fget("last_price") or fget("lastPrice")
        if price:
            _set_field(snap, "price", float(price), "yfinance")
            ok = True
        mcap = fget("market_cap") or fget("marketCap")
        if mcap:
            _set_field(snap, "mcap", float(mcap), "yfinance")
        yh, yl = fget("year_high") or fget("yearHigh"), fget("year_low") or fget("yearLow")
        if yh:
            _set_field(snap, "week52_high", float(yh), "yfinance")
        if yl:
            _set_field(snap, "week52_low", float(yl), "yfinance")
        headline_missing = any(
            f not in snap["fields"] for f in ("pe", "forward_pe", "beta", "div_yield")
        )
        if not ok or headline_missing:
            info = ti.info or {}
            for name, v in {
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "name": info.get("shortName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "eps_ttm": info.get("trailingEps"),
                "div_yield": (info.get("dividendYield") or 0) * 100.0 if info.get("dividendYield") else None,
                "beta": info.get("beta"),
                "avg_volume": info.get("averageVolume"),
            }.items():
                before = len(snap["fields"])
                _set_field(snap, name, v, "yfinance")
                ok = ok or len(snap["fields"]) > before
    except Exception as exc:  # noqa: BLE001
        snap["errors"].append(f"yfinance: {str(exc)[:100]}")
    return ok


# ------------------------------------------------------------------ twelvedata

def _td_fill(snap: dict, symbol: str, key: str) -> bool:
    data, err = _get_json(
        "https://api.twelvedata.com/quote",
        params={"symbol": symbol, "apikey": key},
    )
    if err or not isinstance(data, dict):
        snap["errors"].append(f"twelvedata: {err or 'bad payload'}")
        return False
    _quota_use("twelvedata")
    fields = {
        "price": _num(data.get("close")),
        "name": data.get("name"),
        "exchange": data.get("exchange"),
        "currency": data.get("currency"),
        "avg_volume": _num(data.get("average_volume")),
    }
    got = False
    for name, v in fields.items():
        before = len(snap["fields"])
        _set_field(snap, name, v, "twelvedata")
        got = got or len(snap["fields"]) > before
    return got


# ------------------------------------------------------------------ alphavantage
#
# Alpha Vantage free tier: GLOBAL_QUOTE + EARNINGS remain free; OVERVIEW and
# the fundamentals/statements endpoints became premium-only. News sentiment
# (NEWS_SENTIMENT) went premium too, so news_client does not use AV anymore.

def _av_unusable(data) -> str | None:
    """Any Information/Note payload from AV means no usable data."""
    info = str(data.get("Information") or data.get("Note") or "")
    if not info:
        return None
    if "premium endpoint" in info:
        return "premium endpoint"
    if "spreading out" in info or "rate limit" in info.lower():
        return "rate limited"
    return info[:60]


_AV_RETRY_SLEEP_S = 2.5  # AVs soft rate-limit recovers within seconds


def _av_query(params: dict, key: str, retries: int = 2):
    """_get_json with a small retry on AV's 'spreading out' refusals.

    Returns (data, err): data is the JSON dict, err the final failure string.
    'minute-budget' failures give up immediately; everything else retries.
    """
    last = ""
    for attempt in range(max(1, retries)):
        if attempt:
            time.sleep(_AV_RETRY_SLEEP_S)
        data, err = _get_json(
            "https://www.alphavantage.co/query",
            params={"apikey": key, **params},
        )
        if data is not None:
            msg = _av_unusable(data)
            if msg is None:
                return data, None
            if msg == "premium endpoint":
                return None, "premium endpoint"
            last = msg  # rate limited — worth a retry
            continue
        last = err or "bad payload"
        if last == "minute-budget":
            break
    return None, last


def _av_fill(snap: dict, symbol: str, key: str) -> bool:
    """Last-resort fills from the two endpoints AV still gives for free.

    AV's gateway refuses back-to-back calls ("Please consider spreading out
    your free API requests"), so we only spend calls on genuinely missing
    pieces and space the quote/earnings pair out.
    """
    got = False
    need_quote = not any(snap["fields"].get(f) for f in ("price", "prev_close", "change_pct"))
    need_earnings = not snap["fields"].get("eps_ttm") or len(snap.get("quarters", [])) < 5

    did_quote = False
    if need_quote and _minute_ok("alphavantage", 5):
        q, err = _av_query({"function": "GLOBAL_QUOTE", "symbol": symbol}, key)
        if err or not isinstance(q, dict):
            snap["errors"].append(f"alphavantage quote: {err or 'bad payload'}")
        else:
            _quota_use("alphavantage")
            did_quote = True
            row = q.get("Global Quote") or {}
            fields = {
                "price": _num(row.get("05. price")),
                "prev_close": _num(row.get("08. previous close")),
                "change_pct": _num(str(row.get("10. change percent", "")).replace("%", "")),
            }
            for name, v in fields.items():
                before = len(snap["fields"])
                _set_field(snap, name, v, "alphavantage")
                got = got or len(snap["fields"]) > before

    if need_earnings and _minute_ok("alphavantage", 5):
        if did_quote:
            time.sleep(_AV_RETRY_SLEEP_S)
        e, err = _av_query({"function": "EARNINGS", "symbol": symbol}, key)
        if err or not isinstance(e, dict):
            snap["errors"].append(f"alphavantage earnings: {err or 'bad payload'}")
        else:
            _quota_use("alphavantage")
            rows = e.get("quarterlyEarnings") or []
            eps = []
            for r in rows:
                v = _num(r.get("reportedEPS"))
                if v is not None:
                    eps.append(v)
            if len(eps) >= 4:
                before = len(snap["fields"])
                _set_field(snap, "eps_ttm", round(sum(eps[:4]), 4), "alphavantage")
                got = got or len(snap["fields"]) > before
            have = {str(r.get("end")) for r in snap.get("quarters", [])}
            new_rows = []
            for r in rows[:5]:
                end = str(r.get("fiscalDateEnding") or "")[:10]
                if end and end not in have:
                    have.add(end)
                    new_rows.append({"end": end, "eps": _num(r.get("reportedEPS")), "src": "alphavantage"})
            if new_rows:
                    all_q = sorted(snap.get("quarters", []) + new_rows, key=lambda r: r["end"])
                    snap["quarters"] = all_q[-8:]
                    got = True
    return got


# ------------------------------------------------------------------ orchestration

def refresh_symbol(symbol: str, *, force: bool = False) -> dict | None:
    """Fetch/merge one stock's decision snapshot. Returns the snapshot or None."""
    symbol = symbol.upper()
    snap = read_snapshot(symbol)
    if snap and is_fresh(snap) and not force:
        return snap
    if snap is None:
        snap = _new_snapshot(symbol, "stock")
    else:
        snap["updated"] = datetime.now(NY).isoformat(timespec="seconds")
        snap["sources_used"] = []
        snap["errors"] = []

    keys = os.environ
    quota = quota_status()

    def safe(label: str, fn, *args):
        """One provider failing must never abort the whole refresh."""
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            snap["errors"].append(f"{label} internal: {str(exc)[:100]}")
            return False

    # 1) SEC EDGAR (keyless, authoritative statements)
    safe("sec_edgar", _edgar_fill, snap, symbol)
    if any("sec_edgar" == f.get("src") for f in snap["fields"].values()) or snap["quarters"]:
        snap["sources_used"].append("sec_edgar")

    # 2) FMP (profile, key metrics, quarterly statements); call counting is
    #    internal to _fmp_get — news shares the same daily pool.
    if quota["fmp"]["ok"] and keys.get("FMP_API_KEY"):
        if safe("fmp", _fmp_fill, snap, symbol, keys["FMP_API_KEY"]):
            snap["sources_used"].append("fmp")

    # 3) Finnhub (metrics + earnings calendar)
    if keys.get("FINNHUB_API_KEY"):
        got_metrics = safe("finnhub", _fh_fill, snap, symbol, keys["FINNHUB_API_KEY"])
        got_earnings = safe("finnhub", _fh_earnings, snap, symbol, keys["FINNHUB_API_KEY"])
        if (got_metrics or got_earnings) and "finnhub" not in snap["sources_used"]:
            snap["sources_used"].append("finnhub")

    # 4) yfinance fallback (no-key, light)
    needs_yf = any(
        name not in snap["fields"]
        for name in ("price", "mcap", "name", "sector", "pe")
    )
    if needs_yf and safe("yfinance", _yf_fill, snap, symbol):
        snap["sources_used"].append("yfinance")

    # 5) Twelve Data price fallback
    if "price" not in snap["fields"] and quota["twelvedata"]["ok"] and keys.get("TWELVEDATA_API_KEY"):
        if safe("twelvedata", _td_fill, snap, symbol, keys["TWELVEDATA_API_KEY"]):
            snap["sources_used"].append("twelvedata")

    # 6) Alpha Vantage last resort
    av = av_key()
    if quota["alphavantage"]["ok"] and av:
        if safe("alphavantage", _av_fill, snap, symbol, av):
            snap["sources_used"].append("alphavantage")

    snap = _finalize(snap)
    return snap


CRYPTO_MAP = {
    "BTC-USD": "bitcoin", "ETH-USD": "ethereum", "SOL-USD": "solana",
    "XRP-USD": "ripple", "ADA-USD": "cardano", "DOGE-USD": "dogecoin",
    "BNB-USD": "binancecoin", "AVAX-USD": "avalanche-2", "DOT-USD": "polkadot",
    "LINK-USD": "chainlink", "LTC-USD": "litecoin", "MATIC-USD": "matic-network",
    "TRX-USD": "tron", "SHIB-USD": "shiba-inu", "TON-USD": "the-open-network",
}


def refresh_crypto(symbol: str, *, force: bool = False) -> dict | None:
    """Crypto snapshot: CoinGecko free public market data (keyless)."""
    symbol = symbol.upper()
    snap = read_snapshot(symbol)
    if snap and is_fresh(snap) and not force:
        return snap
    if snap is None:
        snap = _new_snapshot(symbol, "crypto")
    else:
        snap["updated"] = datetime.now(NY).isoformat(timespec="seconds")
        snap["sources_used"] = []
        snap["errors"] = []

    coin_id = CRYPTO_MAP.get(symbol, symbol.split("-")[0].lower())
    data, err = _get_json(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd", "ids": coin_id, "per_page": 5,
            "sparkline": "false", "price_change_percentage": "24h,7d,30d",
        },
    )
    if err or not isinstance(data, list) or not data:
        snap["errors"].append(f"coingecko: {err or 'not found'}")
    else:
        row = data[0]
        for name, v in {
            "name": row.get("name"),
            "price": _num(row.get("current_price")),
            "mcap": _num(row.get("market_cap")),
            "rank": row.get("market_cap_rank"),
            "volume_24h": _num(row.get("total_volume")),
            "change_24h_pct": _num(row.get("price_change_percentage_24h")),
            "change_7d_pct": _num(row.get("price_change_percentage_7d")),
            "change_30d_pct": _num(row.get("price_change_percentage_30d")),
            "ath": _num(row.get("ath")),
            "ath_change_pct": _num(row.get("ath_change_percentage")),
            "supply_circulating": _num(row.get("circulating_supply")),
            "supply_total": _num(row.get("total_supply")),
        }.items():
            _set_field(snap, name, v, "coingecko")
        snap["sources_used"].append("coingecko")
    snap = _finalize(snap)
    return snap


def _finalize(snap: dict) -> dict:
    snap.pop("_etc", None)
    snap["sources_used"] = sorted(set(snap.get("sources_used", [])))
    try:
        _atomic_write(_snapshot_path(snap["symbol"]), json.dumps(snap, indent=1))
    except OSError:
        pass
    return snap


def refresh_list(symbols: list[str], *, force: bool = False) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for symbol in symbols:
        if symbol.endswith("-USD"):
            out[symbol] = refresh_crypto(symbol, force=force)
        else:
            out[symbol] = refresh_symbol(symbol, force=force)
    return out
