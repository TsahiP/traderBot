"""Trade plan (step 5): stop, round break, sizing, TP1/TP2, R:R, holding."""
import analyst.trade_plan as tp


def _smc(demand_lo=100.0, demand_hi=103.0, mid=110.0, lo=100.0, hi=120.0,
         supply_lo=116.0, supply_hi=118.0):
    return {
        "ok": True,
        "price": 108.0,
        "range": {"lo": lo, "hi": hi, "mid": mid} if mid is not None else None,
        "demand_below": {"lo": demand_lo, "hi": demand_hi} if demand_lo is not None else None,
        "supply_above": {"lo": supply_lo, "hi": supply_hi} if supply_lo is not None else None,
        "wall": {"price": supply_lo, "distance_pct": 7.4},
    }


def test_round_break_parks_below_round_number():
    # 100.42 -> highest X.90 <= raw is 99.90 (one dime under the $100 level)
    assert tp.round_break(100.42, 100.42) == 99.90
    # raw already just under $99.00 stays put
    assert tp.round_break(98.92, 100.0) == 98.92
    # raw exactly on a round level must move off it
    assert tp.round_break(100.0, 100.0) == 99.90
    # expensive stocks shade by whole dollars
    assert tp.round_break(563.24, 563.24) == 562.00


def test_position_size_floor():
    s = tp.position_size(entry=100.0, stop=95.0, risk_usd=120.0)
    assert s["shares"] == 24          # floor(120 / 5)
    assert s["risk_per_share"] == 5.0
    assert s["risk_usd_actual"] == 120.0
    assert s["position_value"] == 2400.0
    zero = tp.position_size(100.0, 105.0, 500.0)
    assert zero["shares"] == 0        # stop above entry -> no size


def test_plan_full_structure():
    smc = _smc()
    plan = tp.build_trade_plan(
        price=108.0, smc=smc, atr=4.0, risk_usd=300.0,
    )
    # stop = min(OB lo 100, EQ 110) - 0.25*ATR = 99.0 -> round break -> 98.90
    assert plan["stop"] == 98.90
    assert plan["risk_per_share"] == 9.1
    assert plan["shares"] == 32       # floor(300 / 9.1)
    assert plan["tp1"]["price"] == 118.0    # edge of supply block
    assert plan["tp2"]["price"] == 120.0    # swing high above TP1
    assert plan["tp1"]["rr"] == round((118 - 108) / 9.1, 2)
    assert plan["combined_rr"] is not None
    assert plan["breakeven_after_tp1"] is True
    assert plan["mode"] == "cash_entry"
    assert plan["flags"] == []


def test_plan_no_supply_block_falls_back_to_swing_high():
    smc = _smc(supply_lo=None, supply_hi=None)
    plan = tp.build_trade_plan(price=108.0, smc=smc, atr=4.0, risk_usd=300.0)
    assert plan["tp1"]["price"] == 120.0
    assert plan["tp2"]["price"] is None
    assert "no_supply_block" in plan["flags"]


def test_plan_no_structure_atr_fallback():
    plan = tp.build_trade_plan(price=50.0, smc={"ok": False}, atr=2.0, risk_usd=2000.0)
    assert plan["stop"] == 45.9     # raw 50 - 2*ATR then shifted a dime under $46
    assert plan["stop_basis"] == "atr_fallback"
    assert "no_structure" in plan["flags"]


def test_plan_tp1_rr_flag_when_supply_sits_close():
    # supply edge only +2 above entry, stop ~9 below -> rr1 < 1
    smc = _smc(supply_lo=108.5, supply_hi=110.0)
    plan = tp.build_trade_plan(price=108.0, smc=smc, atr=4.0, risk_usd=300.0)
    assert plan["tp1"]["price"] == 110.0
    assert "tp1_rr_below_1" in plan["flags"]


def test_plan_holding_block():
    smc = _smc(demand_lo=100.0, demand_hi=103.0)
    plan = tp.build_trade_plan(
        price=108.0, smc=smc, atr=4.0, risk_usd=300.0,
        holding={"qty": 50, "avg_price": 105.0},
    )
    h = plan["holding"]
    assert plan["mode"] == "holding"
    assert h["qty"] == 50 and h["avg_price"] == 105.0
    assert h["pnl_usd"] == 150.0
    assert h["pnl_pct"] == round(150 / 5250 * 100, 2)
    assert h["stop_breakeven_or_better"] is False   # stop 98.90 < avg 105
    assert h["plan_only_if_trigger"] is True

    plan_zero = tp.build_trade_plan(
        price=108.0, smc=smc, atr=4.0, risk_usd=300.0,
        holding={"qty": 50, "avg_price": 96.0},
    )
    assert plan_zero["holding"]["stop_breakeven_or_better"] is True


def test_position_size_with_account_caps():
    s = tp.position_size(entry=100.0, stop=95.0, risk_usd=500.0,
                         account={"equity": 20_000.0, "max_position_pct": 25.0})
    assert s["shares"] == 50 and s["limited_by"] == "position_cap"   # 5000 / 100
    assert s["risk_usd_actual"] == 250.0
    plain = tp.position_size(entry=100.0, stop=95.0, risk_usd=120.0)
    assert plain["limited_by"] == "max_loss"


def test_ratio_plan_derives_stop_from_target():
    smc = {"range": {"lo": 90.0, "hi": 130.0, "mid": 110.0}, "supply_above": {"hi": 124.0},
           "demand_below": {"lo": 95.0}}
    plan = tp.build_trade_plan(100.0, smc, 2.0, 250.0, invest_usd=2000.0, target_rr=3.0)
    assert plan["mode"] == "ratio" and plan["tp1"]["price"] == 124.0
    assert plan["stop"] == 92.0            # risk = (124-100)/3 = 8
    assert plan["tp1"]["rr"] == 3.0 and plan["combined_rr"] == 3.0
    assert plan["shares"] == 20 and plan["position_value"] == 2000.0
    assert plan["risk_usd_actual"] == 160.0 and plan["potential_profit_usd"] == 480.0
    assert plan["stop_basis"] == "ratio" and plan["tp2"]["price"] is None
    assert "stop_far_atr" not in plan["flags"]  # 8 / ATR 2 = 4.0: only strictly above 4.0 is far
    plan = tp.build_trade_plan(100.0, smc, 1.0, 250.0, invest_usd=2000.0)
    assert "stop_far_atr" in plan["flags"]       # 8 ATRs


def test_ratio_plan_fallbacks_and_flags():
    # no structure above price -> ATR distance, target placed by ratio
    plan = tp.build_trade_plan(100.0, {}, 2.0, 250.0, invest_usd=1000.0)
    assert plan["stop"] == 96.0 and plan["tp1"]["price"] == 112.0
    assert "target_by_ratio" in plan["flags"] and plan["tp1"]["rr"] == 3.0
    # target hugging the price -> noise stop is flagged
    smc = {"range": {"lo": 90.0, "hi": 100.6, "mid": 95.0}, "supply_above": {"hi": 100.6}}
    plan = tp.build_trade_plan(100.0, smc, 2.0, 250.0, invest_usd=1000.0)
    assert "stop_tight_atr" in plan["flags"]
    # budget below one share
    plan = tp.build_trade_plan(100.0, {}, 2.0, 250.0, invest_usd=50.0)
    assert plan["shares"] == 0 and "invest_below_price" in plan["flags"]
    # without invest_usd the classic plan is untouched
    assert tp.build_trade_plan(100.0, {}, 2.0, 250.0).get("mode") != "ratio"
