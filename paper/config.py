"""paperbot configuration: `output/paperbot_config.json`.

The file is re-read on every scheduler wake, like signal_config.json, so an
edit applies without a restart. A missing file is written with the defaults;
a broken one never stops the bot - `load_config` hands back the last good
config plus the error, and the bot publishes it.

Top-level sections a file leaves out are taken from the defaults, section by
section; a file that has `runs` owns the whole list. Unknown keys are errors,
so a typo never silently switches a limit off. `config_hash` tags every
decision and trade with the settings that produced it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import tools.scanner as scanner
import core.strategies as strategies
from market.market_symbols import is_valid_symbol, normalize_symbol

LLM_MODES = ("off", "shadow", "veto")
LLM_PENDING_POLICIES = ("proceed", "skip")
APPROVAL_MODES = ("auto", "manual")
STYLES = ("day", "swing")
TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "1d")
ENTRY_KINDS = ("orb", "strategy", "patterns")
DISCORD_EVENTS = ("proposal", "fill", "trade.closed", "error", "bot.offline", "day.summary")
SCORE_KEYS = ("trend", "volume", "smc", "rel_strength", "market")
RUN_RISK_KEYS = ("max_loss_per_trade_usd", "max_daily_loss_usd", "max_trades_per_day",
                 "max_position_pct", "min_reward_risk")
GLOBAL_RISK_NUMBERS = ("max_daily_loss_usd", "max_trades_per_day", "max_open_positions",
                       "max_open_risk_usd", "max_short_exposure_pct", "cash_buffer_pct")
GLOBAL_RISK_TIMES = ("no_entries_before", "no_entries_after", "flat_by")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_RUN_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ConfigError(ValueError):
    """A config value is wrong; the message names the field."""


def _risk(loss: float, day: float, trades: int, pos_pct: float = 25.0) -> dict:
    return {"max_loss_per_trade_usd": loss, "max_daily_loss_usd": day,
            "max_trades_per_day": trades, "max_position_pct": pos_pct}


def default_config() -> dict:
    """The spec's §3 config. Phase 1 trades swing_sma; the rest ship disabled."""
    return {
        "llm_mode": "shadow",
        "llm_pending_policy": "proceed",
        "approval_mode": "auto",
        "approval_ttl_bars": 1,
        "approval_max_drift_pct": 0.3,
        "universe": {
            "watchlists": "all",
            "scanner": {"enabled": True, "at": "09:00",
                        "setups": ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold_bounce"],
                        "max_add": 6},
        },
        "global_risk": {
            "max_daily_loss_usd": 500.0, "max_trades_per_day": 6, "max_open_positions": 5,
            "max_open_risk_usd": 600.0, "max_short_exposure_pct": 50.0, "cash_buffer_pct": 10.0,
            "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:55",
        },
        "score_weights": {"trend": 30, "volume": 15, "smc": 25, "rel_strength": 15, "market": 15},
        "runs": [
            {"id": "day_orb", "enabled": False, "style": "day", "timeframe": "5m", "symbols": None,
             "entry": {"kind": "orb", "or_minutes": 15, "min_volume_x": 1.0},
             "exit": {"stop": "or-mid", "target_r": 2.0},
             "allow_short": True, "min_score": 55, "timemachine": True, "risk": _risk(50.0, 150.0, 3)},
            {"id": "day_vwap", "enabled": False, "style": "day", "timeframe": "5m", "symbols": None,
             "entry": {"kind": "strategy", "strategy": "vwap_reversion",
                       "params": {"deviation_pct": 0.8, "exit_pct": 0.2}},
             "exit": {"stop": "atr:1.5", "target_r": 2.0},
             "allow_short": True, "min_score": 50, "timemachine": True, "risk": _risk(50.0, 150.0, 3)},
            {"id": "day_rsi", "enabled": False, "style": "day", "timeframe": "15m", "symbols": None,
             "entry": {"kind": "strategy", "strategy": "rsi_mean_reversion",
                       "params": {"rsi_period": 14, "oversold": 30, "overbought": 70, "exit_level": 50}},
             "exit": {"stop": "atr:1.5", "target_r": 2.0},
             "allow_short": True, "min_score": 50, "timemachine": False, "risk": _risk(50.0, 150.0, 3)},
            {"id": "swing_sma", "enabled": True, "style": "swing", "timeframe": "1d", "symbols": ["SPY"],
             "entry": {"kind": "strategy", "strategy": "sma_crossover", "params": {"fast": 10, "slow": 50}},
             "exit": {"stop": "atr:2", "target_r": 2.0, "exit_on_opposite_signal": True},
             "allow_short": False, "min_score": 0, "timemachine": False, "risk": {"max_loss_per_trade_usd": 200.0}},
            {"id": "swing_patterns", "enabled": False, "style": "swing", "timeframe": "1d", "symbols": None,
             "entry": {"kind": "patterns", "patterns": "watchlist"},
             "exit": {"stop": "structure", "target_r": 2.0},
             "allow_short": True, "min_score": 60, "timemachine": False, "risk": {"max_loss_per_trade_usd": 150.0}},
        ],
        "discord": {"route": "paper",
                    "events": ["proposal", "fill", "trade.closed", "error", "bot.offline", "day.summary"]},
    }


# ---------------------------------------------------------------- field checks

def _fail(where: str, msg: str):
    raise ConfigError(f"{where}: {msg}")


def _only_keys(obj, allowed, where: str) -> dict:
    if not isinstance(obj, dict):
        _fail(where, "must be an object")
    extra = sorted(set(obj) - set(allowed))
    if extra:
        _fail(where, f"unknown key(s) {', '.join(extra)}")
    return obj


def _enum(value, options, where: str):
    if value not in options:
        _fail(where, f"{value!r} is not one of {', '.join(options)}")
    return value


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        _fail(where, "must be true or false")
    return value


def _number(value, where: str, *, minimum=0.0, strict=False, integer=False, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(where, "must be a number")
    if value < minimum or (strict and value == minimum):
        _fail(where, f"must be {'over' if strict else 'at least'} {minimum:g}")
    if integer:
        if int(value) != value:
            _fail(where, "must be a whole number")
        return int(value)
    return float(value)


def _hhmm(value, where: str, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _HHMM.match(value):
        _fail(where, f"{value!r} is not an HH:MM time")
    return value


def parse_stop_rule(rule) -> tuple[str, float | None]:
    """'or-mid' / 'or-far' / 'structure' / 'atr:K' / 'pct:X' -> (kind, value)."""
    if rule in ("or-mid", "or-far", "structure"):
        return rule, None
    kind, _, raw = str(rule).partition(":")
    if kind in ("atr", "pct") and raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            return kind, value
    raise ConfigError(f"stop rule {rule!r}: use or-mid, or-far, structure, atr:K or pct:X")


# ---------------------------------------------------------------- sections

def _universe(raw: dict) -> dict:
    _only_keys(raw, ("watchlists", "scanner"), "universe")
    wl = raw["watchlists"]
    if wl != "all" and not (isinstance(wl, list) and all(isinstance(w, str) and w for w in wl)):
        _fail("universe.watchlists", 'must be "all" or a list of watchlist ids')
    sc = _only_keys(raw["scanner"], ("enabled", "at", "setups", "max_add"), "universe.scanner")
    setups = sc["setups"]
    if not isinstance(setups, list):
        _fail("universe.scanner.setups", "must be a list")
    for s in setups:
        _enum(s, scanner.SETUPS, "universe.scanner.setups")
    return {
        "watchlists": wl,
        "scanner": {
            "enabled": _bool(sc["enabled"], "universe.scanner.enabled"),
            "at": _hhmm(sc["at"], "universe.scanner.at"),
            "setups": list(setups),
            "max_add": _number(sc["max_add"], "universe.scanner.max_add", integer=True),
        },
    }


def _global_risk(raw: dict) -> dict:
    _only_keys(raw, GLOBAL_RISK_NUMBERS + GLOBAL_RISK_TIMES, "global_risk")
    out = {}
    for key in GLOBAL_RISK_NUMBERS:
        out[key] = _number(raw.get(key), f"global_risk.{key}", optional=True,
                           integer=key in ("max_trades_per_day", "max_open_positions"))
    for key in GLOBAL_RISK_TIMES:
        out[key] = _hhmm(raw.get(key), f"global_risk.{key}", optional=key != "flat_by")
    return out


def _score_weights(raw: dict) -> dict:
    _only_keys(raw, SCORE_KEYS, "score_weights")
    return {k: _number(raw.get(k, 0), f"score_weights.{k}") for k in SCORE_KEYS}


def _discord(raw: dict) -> dict:
    _only_keys(raw, ("route", "events"), "discord")
    if not isinstance(raw["route"], str) or not raw["route"]:
        _fail("discord.route", "must be a non-empty string")
    events = raw["events"]
    if not isinstance(events, list):
        _fail("discord.events", "must be a list")
    for e in events:
        _enum(e, DISCORD_EVENTS, "discord.events")
    return {"route": raw["route"], "events": list(events)}


def _entry(raw, run: dict, where: str) -> dict:
    kind = _enum(_only_keys(raw, ("kind", "or_minutes", "min_volume_x", "strategy", "params", "patterns"),
                            where).get("kind"), ENTRY_KINDS, f"{where}.kind")
    if kind == "orb":
        if run["style"] != "day" or run["timeframe"] not in ("1m", "5m", "15m", "30m"):
            _fail(where, "orb is a day setup on 1m-30m bars")
        return {"kind": "orb",
                "or_minutes": _number(raw.get("or_minutes", 15), f"{where}.or_minutes",
                                      minimum=5, integer=True),
                "min_volume_x": _number(raw.get("min_volume_x", 0.0), f"{where}.min_volume_x")}
    if kind == "strategy":
        sid = raw.get("strategy")
        spec = strategies.STRATEGIES.get(sid)
        if spec is None:
            _fail(f"{where}.strategy", f"unknown strategy {sid!r}")
        if run["timeframe"] not in spec["timeframes"]:
            _fail(f"{run['id']}.timeframe", f"{sid} does not run on {run['timeframe']}")
        try:
            params = strategies.parse_params(spec, raw.get("params") or {})
        except ValueError as exc:
            _fail(f"{where}.params", str(exc))
        return {"kind": "strategy", "strategy": sid, "params": params}
    patterns = raw.get("patterns", "watchlist")
    if patterns != "watchlist":
        _fail(f"{where}.patterns", 'only "watchlist" is supported (the watchlist\'s own patterns)')
    return {"kind": "patterns", "patterns": patterns}


def _exit(raw, entry: dict, where: str) -> dict:
    _only_keys(raw, ("stop", "target_r", "exit_on_opposite_signal"), where)
    kind, _ = parse_stop_rule(raw.get("stop"))
    if kind in ("or-mid", "or-far") and entry["kind"] != "orb":
        _fail(f"{where}.stop", f"{raw['stop']} needs an orb entry")
    out = {"stop": raw["stop"],
           "target_r": _number(raw.get("target_r"), f"{where}.target_r", strict=True)}
    if "exit_on_opposite_signal" in raw:
        out["exit_on_opposite_signal"] = _bool(raw["exit_on_opposite_signal"],
                                               f"{where}.exit_on_opposite_signal")
    return out


def _run(raw, index: int) -> dict:
    where = f"runs[{index}]"
    _only_keys(raw, ("id", "enabled", "style", "timeframe", "symbols", "entry", "exit",
                     "allow_short", "min_score", "timemachine", "risk"), where)
    rid = raw.get("id")
    if not isinstance(rid, str) or not _RUN_ID.match(rid):
        _fail(f"{where}.id", f"{rid!r} must be lowercase letters, digits and _")
    where = rid
    run = {"id": rid,
           "enabled": _bool(raw.get("enabled", True), f"{where}.enabled"),
           "style": _enum(raw.get("style"), STYLES, f"{where}.style"),
           "timeframe": _enum(raw.get("timeframe"), TIMEFRAMES, f"{where}.timeframe")}
    if run["style"] == "swing" and run["timeframe"] != "1d":
        _fail(f"{where}.timeframe", "swing runs trade daily bars")
    symbols = raw.get("symbols")
    if symbols is not None:
        if not isinstance(symbols, list) or not symbols:
            _fail(f"{where}.symbols", "must be null (today's universe) or a list")
        bad = [s for s in symbols if not isinstance(s, str) or not is_valid_symbol(s)]
        if bad:
            _fail(f"{where}.symbols", f"invalid symbol(s) {bad}")
        symbols = list(dict.fromkeys(normalize_symbol(s) for s in symbols))
    run["symbols"] = symbols
    run["entry"] = _entry(raw.get("entry"), run, f"{where}.entry")
    run["exit"] = _exit(raw.get("exit"), run["entry"], f"{where}.exit")
    run["allow_short"] = _bool(raw.get("allow_short", False), f"{where}.allow_short")
    min_score = _number(raw.get("min_score", 0), f"{where}.min_score")
    if min_score > 100:
        _fail(f"{where}.min_score", "must be 0-100")
    run["min_score"] = min_score
    run["timemachine"] = _bool(raw.get("timemachine", False), f"{where}.timemachine")
    risk_raw = _only_keys(raw.get("risk", {}), RUN_RISK_KEYS, f"{where}.risk")
    run["risk"] = {k: _number(v, f"{where}.risk.{k}", optional=True,
                              integer=k == "max_trades_per_day")
                   for k, v in risk_raw.items()}
    return run


# ---------------------------------------------------------------- public API

def validate_config(raw) -> dict:
    """Clean copy of `raw` with missing sections taken from the defaults.
    Raises ConfigError naming the first bad field."""
    if not isinstance(raw, dict):
        raise ConfigError("config: must be a JSON object")
    defaults = default_config()
    _only_keys(raw, defaults, "config")
    merged = copy.deepcopy(defaults)
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(defaults[key], dict):
            section = copy.deepcopy(defaults[key])
            section.update(value)
            merged[key] = section
        else:
            merged[key] = copy.deepcopy(value)

    runs = merged["runs"]
    if not isinstance(runs, list):
        _fail("runs", "must be a list")
    clean_runs = [_run(r, i) for i, r in enumerate(runs)]
    ids = [r["id"] for r in clean_runs]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        _fail("runs", f"duplicate id(s) {', '.join(dupes)}")

    return {
        "llm_mode": _enum(merged["llm_mode"], LLM_MODES, "llm_mode"),
        "llm_pending_policy": _enum(merged["llm_pending_policy"], LLM_PENDING_POLICIES,
                                    "llm_pending_policy"),
        "approval_mode": _enum(merged["approval_mode"], APPROVAL_MODES, "approval_mode"),
        "approval_ttl_bars": _number(merged["approval_ttl_bars"], "approval_ttl_bars",
                                     minimum=1, integer=True),
        "approval_max_drift_pct": _number(merged["approval_max_drift_pct"], "approval_max_drift_pct",
                                          strict=True),
        "universe": _universe(merged["universe"]),
        "global_risk": _global_risk(merged["global_risk"]),
        "score_weights": _score_weights(merged["score_weights"]),
        "runs": clean_runs,
        "discord": _discord(merged["discord"]),
    }


def config_hash(cfg: dict) -> str:
    """First 10 hex chars of sha1 over the canonical JSON (key order ignored)."""
    canon = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:10]


def _write(path: Path, cfg: dict) -> None:
    from signaling.signals import _atomic_write  # retries the replace while a reader holds the file

    _atomic_write(Path(path), json.dumps(cfg, indent=2))


def load_config(path: Path, last_good: dict | None = None) -> tuple[dict, str | None]:
    """(config, error). Missing file -> defaults written. Broken file -> the
    last good config (or the defaults) and the error; the file is left alone."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        cfg = default_config()
        _write(path, cfg)
        return cfg, None
    except OSError as exc:
        return last_good or default_config(), f"config unreadable: {exc}"
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return last_good or default_config(), f"config is not valid JSON: {exc}"
    try:
        return validate_config(raw), None
    except ConfigError as exc:
        return last_good or default_config(), str(exc)


def set_approval(path: Path, mode: str) -> dict:
    """The UI's "Ask before entering" switch: write approval_mode, keep the rest."""
    _enum(mode, APPROVAL_MODES, "approval_mode")
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = default_config()
    except ValueError as exc:
        raise ConfigError(f"config is not valid JSON: {exc}") from exc
    raw["approval_mode"] = mode
    cfg = validate_config(raw)
    _write(path, cfg)
    return cfg
