"""paperbot config: defaults, validation, last-good fallback, hash, approval switch."""
import copy
import json

import pytest

from paper import config as pcfg


def _write(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def _run(**over):
    run = copy.deepcopy(next(r for r in pcfg.default_config()["runs"] if r["id"] == "swing_sma"))
    run.update(over)
    return run


def _with_runs(*runs):
    cfg = pcfg.default_config()
    cfg["runs"] = list(runs)
    return cfg


def test_defaults_validate_and_are_stable():
    cfg = pcfg.default_config()
    assert pcfg.validate_config(cfg) == cfg
    assert cfg["approval_mode"] == "auto"           # the bot just executes by default
    assert cfg["llm_mode"] in pcfg.LLM_MODES
    enabled = [r["id"] for r in cfg["runs"] if r["enabled"]]
    assert "swing_sma" in enabled


def test_missing_file_writes_defaults(tmp_path):
    path = tmp_path / "paperbot_config.json"
    cfg, err = pcfg.load_config(path)
    assert err is None and cfg == pcfg.default_config()
    assert json.loads(path.read_text(encoding="utf-8")) == cfg


def test_partial_file_is_filled_with_defaults(tmp_path):
    path = tmp_path / "c.json"
    _write(path, {"approval_mode": "manual", "global_risk": {"max_trades_per_day": 2}})
    cfg, err = pcfg.load_config(path)
    assert err is None
    assert cfg["approval_mode"] == "manual"
    assert cfg["global_risk"]["max_trades_per_day"] == 2
    assert cfg["global_risk"]["flat_by"] == pcfg.default_config()["global_risk"]["flat_by"]
    assert cfg["runs"] == pcfg.default_config()["runs"]


def test_bad_json_keeps_last_good_and_does_not_overwrite(tmp_path):
    path = tmp_path / "c.json"
    _write(path, "{not json")
    good = pcfg.default_config()
    good["approval_mode"] = "manual"
    cfg, err = pcfg.load_config(path, last_good=good)
    assert cfg is good and "JSON" in err
    assert path.read_text(encoding="utf-8") == "{not json"


def test_invalid_file_without_last_good_falls_back_to_defaults(tmp_path):
    path = tmp_path / "c.json"
    _write(path, {"llm_mode": "always"})
    cfg, err = pcfg.load_config(path)
    assert cfg == pcfg.default_config()
    assert "llm_mode" in err


@pytest.mark.parametrize("key,value", [
    ("llm_mode", "always"),
    ("llm_pending_policy", "wait"),
    ("approval_mode", "ask"),
    ("approval_ttl_bars", 0),
    ("approval_max_drift_pct", -1),
])
def test_top_level_enums_and_numbers(key, value):
    cfg = pcfg.default_config()
    cfg[key] = value
    with pytest.raises(pcfg.ConfigError, match=key):
        pcfg.validate_config(cfg)


def test_times_must_be_hh_mm():
    cfg = pcfg.default_config()
    cfg["global_risk"]["flat_by"] = "15:5x"
    with pytest.raises(pcfg.ConfigError, match="flat_by"):
        pcfg.validate_config(cfg)
    cfg["global_risk"]["flat_by"] = "25:00"
    with pytest.raises(pcfg.ConfigError, match="flat_by"):
        pcfg.validate_config(cfg)


def test_entry_window_limits_can_be_switched_off():
    cfg = pcfg.default_config()
    cfg["global_risk"]["no_entries_before"] = None
    cfg["global_risk"]["max_open_risk_usd"] = None
    out = pcfg.validate_config(cfg)
    assert out["global_risk"]["no_entries_before"] is None


def test_unknown_keys_are_rejected():
    cfg = pcfg.default_config()
    cfg["global_risk"]["max_lose"] = 5
    with pytest.raises(pcfg.ConfigError, match="max_lose"):
        pcfg.validate_config(cfg)


def test_duplicate_run_ids():
    with pytest.raises(pcfg.ConfigError, match="duplicate"):
        pcfg.validate_config(_with_runs(_run(), _run()))


def test_unknown_strategy_and_unsupported_timeframe():
    bad = _run(entry={"kind": "strategy", "strategy": "moon", "params": {}})
    with pytest.raises(pcfg.ConfigError, match="moon"):
        pcfg.validate_config(_with_runs(bad))
    vwap_daily = _run(entry={"kind": "strategy", "strategy": "vwap_reversion", "params": {}})
    with pytest.raises(pcfg.ConfigError, match="timeframe"):
        pcfg.validate_config(_with_runs(vwap_daily))


def test_strategy_params_are_coerced_and_range_checked():
    run = _run(entry={"kind": "strategy", "strategy": "sma_crossover", "params": {"fast": "12"}})
    out = pcfg.validate_config(_with_runs(run))
    assert out["runs"][0]["entry"]["params"] == {"fast": 12, "slow": 50}
    bad = _run(entry={"kind": "strategy", "strategy": "sma_crossover", "params": {"fast": 0}})
    with pytest.raises(pcfg.ConfigError, match="fast"):
        pcfg.validate_config(_with_runs(bad))


def test_orb_is_a_day_setup_on_intraday_bars():
    orb = _run(id="x", style="swing", timeframe="1d",
               entry={"kind": "orb", "or_minutes": 15, "min_volume_x": 1.0})
    with pytest.raises(pcfg.ConfigError, match="orb"):
        pcfg.validate_config(_with_runs(orb))


@pytest.mark.parametrize("rule,parsed", [
    ("or-mid", ("or-mid", None)),
    ("or-far", ("or-far", None)),
    ("structure", ("structure", None)),
    ("atr:1.5", ("atr", 1.5)),
    ("pct:2", ("pct", 2.0)),
])
def test_stop_rules_parse(rule, parsed):
    assert pcfg.parse_stop_rule(rule) == parsed


@pytest.mark.parametrize("rule", ["atr", "atr:0", "atr:x", "pct:-1", "midpoint", ""])
def test_bad_stop_rules(rule):
    with pytest.raises(pcfg.ConfigError):
        pcfg.parse_stop_rule(rule)


def test_or_stops_need_an_orb_entry():
    run = _run(exit={"stop": "or-mid", "target_r": 2.0})
    with pytest.raises(pcfg.ConfigError, match="or-mid"):
        pcfg.validate_config(_with_runs(run))


def test_run_fields_get_defaults_and_symbols_are_normalized():
    minimal = {"id": "s", "style": "swing", "timeframe": "1d", "symbols": ["spy"],
               "entry": {"kind": "strategy", "strategy": "sma_crossover"},
               "exit": {"stop": "atr:2", "target_r": 2}}
    out = pcfg.validate_config(_with_runs(minimal))["runs"][0]
    assert out["symbols"] == ["SPY"]
    assert out["enabled"] is True and out["allow_short"] is False
    assert out["risk"] == {} and out["min_score"] == 0
    with pytest.raises(pcfg.ConfigError, match="symbol"):
        pcfg.validate_config(_with_runs({**minimal, "symbols": ["NOT A TICKER"]}))


def test_run_risk_keys_are_checked():
    with pytest.raises(pcfg.ConfigError, match="max_lots"):
        pcfg.validate_config(_with_runs(_run(risk={"max_lots": 3})))


def test_discord_events_must_be_known():
    cfg = pcfg.default_config()
    cfg["discord"]["events"] = ["fill", "everything"]
    with pytest.raises(pcfg.ConfigError, match="everything"):
        pcfg.validate_config(cfg)


def test_hash_ignores_key_order_and_tracks_values():
    a = pcfg.default_config()
    b = json.loads(json.dumps(a, sort_keys=True))
    b = dict(reversed(list(b.items())))
    assert pcfg.config_hash(a) == pcfg.config_hash(b)
    assert len(pcfg.config_hash(a)) == 10
    b["approval_mode"] = "manual"
    assert pcfg.config_hash(a) != pcfg.config_hash(b)


def test_set_approval_round_trip(tmp_path):
    path = tmp_path / "c.json"
    pcfg.load_config(path)
    cfg = pcfg.set_approval(path, "manual")
    assert cfg["approval_mode"] == "manual"
    assert pcfg.load_config(path)[0]["approval_mode"] == "manual"
    assert pcfg.set_approval(path, "auto")["approval_mode"] == "auto"


def test_set_approval_rejects_a_bad_mode_and_leaves_the_file(tmp_path):
    path = tmp_path / "c.json"
    pcfg.load_config(path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(pcfg.ConfigError):
        pcfg.set_approval(path, "sometimes")
    assert path.read_text(encoding="utf-8") == before
