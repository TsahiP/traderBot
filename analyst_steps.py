"""Per-step analyst card: one small LLM call per step, Step 5 built in code.

The single-call card made local models loop and mix up JSON fields. Here each
step gets only its own slice of the data (a few hundred tokens), its own
guidance and its own token budget; Step 5 (the execution plan) is rendered
from `trade_plan` in Python, so its numbers are exact by construction. A last
call picks the verdict from the step summaries, and code caps it at WAIT when
the plan carries a quality flag.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from llm_client import vision_user_message

STEP_TOKEN_BUDGET = 6000        # first rung; analyst_context._analysis_call escalates
FRESH_QUARTER_DAYS = 550        # a latest quarter older than this = stale fundamentals
STALE_NOTE = "SEC quarterly fundamentals are stale or inconsistent and were left out"
SEC_DERIVED = ("gross_margin", "operating_margin", "net_margin", "revenue_ttm",
               "net_income_ttm", "eps_ttm", "fcf_ttm", "revenue_growth_yoy")
QUALITY_FLAGS = {"tp1_rr_below_1", "no_structure", "stop_far_atr", "stop_capped",
                 "no_structural_target", "stop_tight_atr", "invest_below_price"}
SMALL_INT = 10                  # counts like "3 of 5" need no source

STEP_TITLES = {
    1: "Step 1 — Catalysts",
    2: "Step 2 — Macro",
    3: "Step 3 — Smart Money",
    4: "Step 4 — Squeeze Momentum",
    5: "Step 5 — Execution plan",
}

COMMON_RULES = """You write ONE section of a briefing card for a retail trader (long-only). You receive a small DATA json block for that section only.

RULES:
1. Every number you write MUST appear in the DATA verbatim. Never invent, round, approximate (~) or extrapolate. Cite the 3-5 most important numbers of the section exactly as given, and say what each implies for the trader - a number without its meaning is useless. Skip any number you are unsure about.
2. A missing / null / errored field is a data gap: state it in one short phrase at most. Never write the words null, None or n/a, never apologize, never say "as an AI".
3. WRITE FOR A HUMAN: 4-6 full Hebrew sentences (60-90 words) that interpret the data. Never paste key:value lists and never use JSON key names (change_pct, price_zone, five_bar_pct, ...).
4. Then ONE final line that starts with "סיכום: " - a short takeaway.
5. Output only the section body: no heading, no code block, no table, no preamble, do not restate these rules. Think briefly, then answer.
6. LANGUAGE: fluent natural Hebrew (RTL). Tickers, numbers, technical terms (ATR, R:R, BOS, CHoCH, OB, FVG, EMA, squeeze) and company names stay in English."""

STEP_GUIDE = {
    1: """SECTION: Step 1 - Catalysts. Say who the company is (one sentence), then valuation vs growth (pe, forward_pe, margins and last_quarters *_yoy_pct when present), today's price behavior (price_action: change, gap, relative volume, close position, absorption/distribution read) and where price sits in the 52-week range. Then what the news headlines push (positive / negative / neutral); an 8-K tag in the news (earnings, leadership, restatement...) is a hard catalyst - name it. Mention the next earnings date if given and any recent signal. If data_gaps lists something, note the gap in one phrase.""",
    2: """SECTION: Step 2 - Macro. One line on the QQQ regime (trend vs EMAs, premium/discount zone), then VIX, oil (WTI/Brent) and the geopolitical headlines -> state your risk-on / risk-off call yourself and what it means for NEW long entries. Finish by saying whether the stock leads or lags SPY (relative_strength_vs_spy: Excess_* per period and leading_periods).""",
    3: """SECTION: Step 3 - Smart Money. Tell the structure story. Cover 1D first, then 15M: trend bias (last BOS / CHoCH side and price), where price sits (price_zone, near equilibrium) relative to the demand block below and the supply block / wall above, and whether open FVGs or equal highs/lows (eqh / eql) act as magnets or risk. Then say where price sits vs the 60-day volume profile (point_of_control, value_area_low/high): inside value = balance, above/below = acceptance or rejection. The attached charts support the read; they add no numbers.""",
    4: """SECTION: Step 4 - Squeeze Momentum (LazyBear). For each timeframe (1D, 15M): is the squeeze on or off, the momentum value vs the previous value and its color (bright green = rising positive, dark green = fading positive, pink/red = negative), and any zero cross. Say what that implies (compression -> expansion, or momentum fading) and whether the two timeframes agree. The attached charts support the read; they add no numbers.""",
}

VERDICT_RULES = """You pick the verdict of a long-only briefing card from the digest below (four section summaries + the execution plan numbers and flags).

OUTPUT exactly two lines:
Line 1: copy ONE pattern - "GO - confidence X/5" or "WAIT - confidence X/5" or "AVOID - confidence X/5" (X is 1-5; never write the whole menu).
Line 2: one short Hebrew sentence giving the reason; any number you cite must appear in the digest verbatim.
If the plan flags are bad (tp1_rr_below_1, no_structure, stop_far_atr, no_supply_block) lean to WAIT/AVOID. Never suggest shorts. Think briefly, then answer."""

FLAG_HE = {
    "tp1_rr_below_1": "יחס הסיכוי/סיכון ביעד הראשון נמוך מ-1 - הרווח הפוטנציאלי קטן מהסיכון",
    "no_supply_block": "אין אזור היצע מעל המחיר, לכן היעד הראשון הוא פסגת הטווח הנוכחי",
    "stop_far_atr": "הסטופ רחוק מדי במונחי ATR",
    "no_structure": "אין מבנה תומך ולכן הסטופ מבוסס ATR בלבד",
    "stop_capped": "הסטופ הוגבל כדי להישאר מתחת למחיר",
    "no_structural_target": "אין יעד מבני ברור",
    "stop_tight_atr": "הסטופ צמוד מדי (פחות מחצי ATR) - רגיש לרעש",
    "target_by_ratio": "אין יעד מבני מעל המחיר, היעד נקבע לפי היחס",
    "invest_below_price": "סכום ההשקעה לא מספיק אפילו למניה אחת",
}
BASIS_HE = {
    "order_block": "אזור ביקוש (order block)",
    "equilibrium": "אמצע הטווח (equilibrium)",
    "swing_low": "שפל הטווח (swing low)",
    "atr_fallback": "ATR (גיבוי)",
    "ratio": "היחס שנבחר (הסטופ נגזר מהיעד)",
}


# ---------------------------------------------------------------- data slices

def _strip_bars(obj):
    """Drop bar indices: they are noise the model likes to quote as numbers."""
    if isinstance(obj, dict):
        return {k: _strip_bars(v) for k, v in obj.items()
                if not (k == "bar" or k.endswith("_bar"))}
    if isinstance(obj, list):
        return [_strip_bars(v) for v in obj]
    return obj


def _fresh_quarters(quarters: list, today: date | None = None) -> list:
    today = today or date.today()
    out = []
    for q in quarters or []:
        try:
            end = datetime.fromisoformat(str(q.get("end"))).date()
        except (TypeError, ValueError):
            continue
        if today - end <= timedelta(days=FRESH_QUARTER_DAYS):
            out.append(q)
    return out


def clean_fundamentals(data: dict, today: date | None = None) -> tuple[dict, list, list[str]]:
    """(fields, quarters, gaps): fundamentals minus stale / inconsistent SEC data.

    When the newest SEC quarter is stale or the TTM block contradicts itself
    (net income above revenue, margins beyond +-100%), every SEC-derived field
    is dropped so the model is never asked to narrate year-old numbers.
    """
    fields = {k: v.get("v") for k, v in (data.get("fields") or {}).items()
              if isinstance(v, dict) and v.get("v") is not None}
    raw_q = data.get("quarters") or []
    quarters = _fresh_quarters(raw_q, today)
    gaps: list[str] = []

    def num(key):
        v = fields.get(key)
        return v if isinstance(v, (int, float)) else None

    rev, ni = num("revenue_ttm"), num("net_income_ttm")
    inconsistent = (rev is not None and ni is not None and ni > rev) or any(
        num(k) is not None and abs(num(k)) > 100 for k in ("gross_margin", "operating_margin", "net_margin"))
    stale = bool(raw_q) and not quarters
    if inconsistent or stale:
        for k in SEC_DERIVED:
            fields.pop(k, None)
        gaps.append(STALE_NOTE)
    div = num("div_yield")
    if div is not None and div > 25:
        fields.pop("div_yield", None)
    desc = fields.get("description")
    if isinstance(desc, str):
        fields["description"] = desc[:200]
    return fields, quarters, gaps


def _clean_news(news: list) -> list:
    out = []
    for n in news or []:
        summary = n.get("summary") or ""
        item = {"ts": n.get("ts"), "source": n.get("source"), "title": n.get("title"),
                "sentiment": n.get("sentiment"), "tags": n.get("tags")}
        if summary and not summary.lstrip().startswith("<"):
            item["summary"] = summary[:160]
        out.append({k: v for k, v in item.items() if v})
    return out


def step_slice(step: int, data: dict) -> dict:
    """The DATA json for one LLM step (a few hundred tokens)."""
    sym = data.get("symbol")
    if step == 1:
        fields, quarters, gaps = clean_fundamentals(data)
        gaps += [str(e) for e in (data.get("snapshot_errors") or [])[:2]]
        return {
            "symbol": sym,
            "fields": fields,
            "last_quarters": quarters,
            "price_action": data.get("price_action"),
            "ex_dividend_date": data.get("ex_dividend"),
            "news": _clean_news(data.get("news")),
            "recent_signals": data.get("recent_signals") or [],
            "data_gaps": gaps,
        }
    if step == 2:
        macro = dict(data.get("macro") or {})
        geo = dict(macro.get("geopolitics") or {})
        geo["headlines"] = [{"source": h.get("source"), "title": h.get("title")}
                            for h in (geo.get("headlines") or [])[:6]]
        if geo:
            macro["geopolitics"] = geo
        macro.pop("updated", None)
        return {"symbol": sym, "macro": macro,
                "relative_strength_vs_spy": data.get("relative_strength")}
    if step == 3:
        return _strip_bars({"symbol": sym, "daily": data.get("smc_daily"),
                            "15m": data.get("smc_15m"),
                            "volume_profile_60d": data.get("volume_profile_daily")})
    if step == 4:
        return {"symbol": sym, "daily": data.get("squeeze_daily"), "15m": data.get("squeeze_15m")}
    raise ValueError(f"no LLM slice for step {step}")


# --------------------------------------------------------------- number guard

_NUM_RE = re.compile(r"(?<![0-9A-Za-z.])(\d+(?:\.\d+)?)(?![0-9A-Za-z])")


def unknown_numbers(text: str, source_json: str) -> list[str]:
    """Numbers in `text` that appear nowhere in the step's DATA (small counts excepted)."""
    bad: list[str] = []
    for m in _NUM_RE.findall(text or ""):
        if "." not in m and int(m) <= SMALL_INT:
            continue
        if m not in source_json and m not in bad:
            bad.append(m)
    return bad


_KEY_RE = re.compile(r"\b[A-Za-z]{2,}(?:_[A-Za-z0-9]+)+\b")


def leaked_keys(text: str) -> list[str]:
    """snake_case JSON key names in prose (forbidden by rule 3)."""
    return list(dict.fromkeys(_KEY_RE.findall(text or "")))


# ------------------------------------------------------------------ LLM steps

def _messages(system: str, user_text: str, images=None) -> list[dict]:
    return [{"role": "system", "content": system}, vision_user_message(user_text, images)]


def _gap(step: int) -> str:
    return "הניתוח לשלב זה לא זמין כרגע (תקלה בהפקת הטקסט).\nסיכום: חסר מידע."


def _ask(call, system: str, user_text: str, images, source_json: str, budget: int) -> str:
    """One call with a single guarded retry on invented numbers or leaked JSON key names."""
    text = _call_with_image_fallback(call, system, user_text, images, budget)
    bad, keys = unknown_numbers(text, source_json), leaked_keys(text)
    if text.strip() and (bad or keys):
        notes = []
        if bad:
            notes.append(f"it cited numbers that are not in the DATA ({', '.join(bad[:8])}) - use only "
                         "numbers copied from the DATA, or leave numbers out")
        if keys:
            notes.append(f"it used JSON key names ({', '.join(keys[:6])}) - describe them in plain Hebrew")
        again = f"{user_text}\n\nYour previous draft was rejected: {'; and '.join(notes)}. Rewrite the section."
        retry = _call_with_image_fallback(call, system, again, images, budget)
        if retry.strip() and (len(unknown_numbers(retry, source_json)) + len(leaked_keys(retry))
                              <= len(bad) + len(keys)):
            text = retry
    return text.strip()


def _call_with_image_fallback(call, system, user_text, images, budget) -> str:
    try:
        return call(_messages(system, user_text, images), max_tokens=budget)
    except Exception:  # noqa: BLE001 - server/model may reject images
        if not images:
            raise
        return call(_messages(system, user_text, None), max_tokens=budget)


def llm_step(step: int, data: dict, call, charts: list | None = None) -> str:
    payload = json.dumps(step_slice(step, data), ensure_ascii=False, default=str)
    system = f"{COMMON_RULES}\n\n{STEP_GUIDE[step]}"
    text = _ask(call, system, f"Write the section for {data['symbol']}.\nDATA:\n{payload}",
                charts, payload, STEP_TOKEN_BUDGET)
    return text or _gap(step)


# ------------------------------------------------------- Step 5 (deterministic)

def _p(x) -> str:
    return f"{float(x):.2f}"


def _render_ratio(plan: dict, flags: list[str], tp1: dict) -> tuple[str, list[str]]:
    """Step 5 for the fixed-ratio plan: amount invested -> shares, stop from the ratio."""
    rr = plan.get("target_rr")
    s = [f"תוכנית לפי יחס 1:{rr:g}: השקעה של {_p(plan['invest_usd'])}$ בכניסה (long) ב-{_p(plan['entry'])}."]
    if plan.get("shares"):
        s.append(f"זה מספיק ל-{plan['shares']} מניות (שווי {_p(plan['position_value'])}$).")
    s.append(f"היעד ב-{_p(tp1['price'])}"
             + (" (יעד מבני)" if "target_by_ratio" not in flags else " (נקבע לפי היחס, אין יעד מבני מעל המחיר)")
             + f" והסטופ נגזר מהיחס: {_p(plan['stop'])}, כלומר סיכון של {_p(plan['risk_per_share'])} למניה"
             + (f" ({plan['atr_atr_multiple']} ATR)." if plan.get("atr_atr_multiple") else ".")
             + " הסטופ הזה מבוסס על היחס ולא על מבנה השוק.")
    if plan.get("shares"):
        s.append(f"סיכון בפועל {_p(plan['risk_usd_actual'])}$ מול רווח פוטנציאלי של "
                 f"{_p(plan['potential_profit_usd'])}$ ביציאה מלאה ביעד.")
    hold = plan.get("holding")
    if isinstance(hold, dict):
        s.append(f"בפוזיציה קיימת: {hold['qty']:g} מניות במחיר ממוצע {_p(hold['avg_price'])}, "
                 f"רווח/הפסד {_p(hold['pnl_usd'])}$ ({hold['pnl_pct']}%).")
    shown = [f for f in flags if f in FLAG_HE]
    if shown:
        s.append("דגלי אזהרה: " + "; ".join(FLAG_HE[f] for f in shown) + ".")
    quality = [f for f in flags if f in QUALITY_FLAGS]
    summary = ("סיכום: התוכנית נושאת דגלי אזהרה - " + ", ".join(FLAG_HE[f] for f in quality[:2]) + "."
               if quality else
               f"סיכום: תוכנית 1:{rr:g} נקייה - סיכון {_p(plan['risk_usd_actual'])}$ מול רווח "
               f"פוטנציאלי {_p(plan['potential_profit_usd'])}$.")
    return " ".join(s) + "\n" + summary, flags


def render_execution(plan: dict | None) -> tuple[str, list[str]]:
    """Hebrew Step 5 body from the trade_plan block -> (text, flags)."""
    if not isinstance(plan, dict) or not plan.get("ok"):
        return ("אין תוכנית ביצוע - חסרים נתוני מחיר או מבנה.\nסיכום: אין תוכנית.", [])
    flags = list(plan.get("flags") or [])
    tp1 = plan.get("tp1") or {}
    tp2 = plan.get("tp2") or {}
    basis = BASIS_HE.get(plan.get("stop_basis"), plan.get("stop_basis")) or "-"
    if "+" in str(plan.get("stop_basis") or ""):
        basis = " + ".join(BASIS_HE.get(b, b) for b in str(plan["stop_basis"]).split("+"))

    if plan.get("mode") == "ratio":
        return _render_ratio(plan, flags, tp1)

    s = [f"כניסה (long) ב-{_p(plan['entry'])}, סטופ ב-{_p(plan['stop'])} על בסיס {basis}: "
         f"סיכון של {_p(plan['risk_per_share'])} למניה"
         + (f" ({plan['atr_atr_multiple']} ATR)." if plan.get("atr_atr_multiple") else ".")]
    if tp1.get("price") is not None:
        rr1 = f", R:R של {tp1['rr']}" if tp1.get("rr") is not None else ""
        s.append(f"יעד ראשון (TP1) ב-{_p(tp1['price'])} - {tp1.get('share_pct', 50)}% מהפוזיציה{rr1}.")
    else:
        s.append("אין יעד ראשון מוגדר.")
    if tp2.get("price") is not None:
        rr2 = f", R:R של {tp2['rr']}" if tp2.get("rr") is not None else ""
        s.append(f"יעד שני (TP2) ב-{_p(tp2['price'])}{rr2}"
                 + (f"; R:R משולב {plan['combined_rr']}." if plan.get("combined_rr") is not None else "."))
    else:
        s.append("אין יעד שני (TP2) - מעל היעד הראשון אין יעד מבני.")
    if plan.get("shares"):
        s.append(f"גודל פוזיציה: {plan['shares']} מניות (שווי {_p(plan['position_value'])}$), "
                 f"סיכון בפועל {_p(plan['risk_usd_actual'])}$. אחרי TP1 מעבירים את הסטופ ל-breakeven.")
    else:
        s.append("תקציב הסיכון לא מספיק אפילו למניה אחת עם הסטופ הזה.")
    hold = plan.get("holding")
    if isinstance(hold, dict):
        s.append(f"בפוזיציה קיימת: {hold['qty']:g} מניות במחיר ממוצע {_p(hold['avg_price'])}, "
                 f"רווח/הפסד {_p(hold['pnl_usd'])}$ ({hold['pnl_pct']}%).")
    if flags:
        s.append("דגלי אזהרה: " + "; ".join(FLAG_HE.get(f, f) for f in flags) + ".")
    quality = [f for f in flags if f in QUALITY_FLAGS]
    summary = ("סיכום: התוכנית נושאת דגלי אזהרה - " + ", ".join(FLAG_HE.get(f, f) for f in quality[:2]) + "."
               if quality else
               f"סיכום: תוכנית נקייה, R:R של {tp1.get('rr')} ביעד הראשון." if tp1.get("rr") is not None
               else "סיכום: תוכנית ללא דגלי איכות.")
    return " ".join(s) + "\n" + summary, flags


# ------------------------------------------------------------------- verdict

_VERDICT_RE = re.compile(r"\b(GO|WAIT|AVOID)\s*-\s*confidence\s*([1-5])\s*/\s*5", re.IGNORECASE)


def _summary_line(section: str) -> str:
    for line in reversed(section.splitlines()):
        if line.strip().startswith("סיכום:"):
            return line.strip()
    return section.strip().splitlines()[-1][:160] if section.strip() else ""


def parse_verdict(text: str, flags: list[str]) -> tuple[str, int, str]:
    """(verdict, confidence, reason); GO is capped to WAIT when the plan has a quality flag."""
    m = _VERDICT_RE.search(text or "")
    if m:
        verdict, conf = m.group(1).upper(), int(m.group(2))
        rest = (text[:m.start()] + text[m.end():]).strip().strip("-:").strip()
        reason = next((ln.strip() for ln in rest.splitlines() if ln.strip()), "")
    else:
        verdict, conf, reason = "WAIT", 2, ""
    quality = [f for f in flags if f in QUALITY_FLAGS]
    if verdict == "GO" and quality:
        verdict, conf = "WAIT", min(conf, 3)
        reason = ""
    if not reason:
        reason = ("דגלי התוכנית מונעים כניסה כרגע: " + ", ".join(quality) + "." if quality
                  else "לא התקבל נימוק מהמודל - ההכרעה זהירה כברירת מחדל.")
    return verdict, conf, reason


def llm_verdict(data: dict, sections: dict[int, str], flags: list[str], call) -> str:
    plan = data.get("trade_plan") if isinstance(data.get("trade_plan"), dict) else {}
    digest = {
        "symbol": data["symbol"],
        "section_summaries": {STEP_TITLES[i]: _summary_line(t) for i, t in sections.items()},
        "plan": {"entry": plan.get("entry"), "stop": plan.get("stop"),
                 "tp1": plan.get("tp1"), "tp2": plan.get("tp2"),
                 "combined_rr": plan.get("combined_rr"), "flags": flags},
    }
    payload = json.dumps(digest, ensure_ascii=False, default=str)
    try:
        text = call(_messages(VERDICT_RULES, f"DIGEST:\n{payload}"), max_tokens=STEP_TOKEN_BUDGET)
    except Exception:  # noqa: BLE001
        text = ""
    verdict, conf, reason = parse_verdict(text, flags)
    return f"{verdict} - confidence {conf}/5\n{reason}"


# --------------------------------------------------------------------- driver

def build_card(data: dict, call, charts: list | None = None) -> str:
    """Whole card: LLM steps 1-4, deterministic step 5, LLM verdict."""
    sections: dict[int, str] = {}
    for step in (1, 2, 3, 4):
        try:
            sections[step] = llm_step(step, data, call, charts if step in (3, 4) else None)
        except Exception:  # noqa: BLE001 - one dead step must not kill the card
            sections[step] = _gap(step)
    sections[5], flags = render_execution(data.get("trade_plan"))
    verdict = llm_verdict(data, sections, flags, call)
    parts = [f"## {STEP_TITLES[i]}\n{sections[i]}" for i in (1, 2, 3, 4, 5)]
    parts.append(f"## Verdict\n{verdict}")
    return "\n\n".join(parts)
