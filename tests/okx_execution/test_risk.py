"""Task 59 sizing and causal management invariants, with synthetic quotes."""
from dataclasses import replace
import pytest

from yoyo.okx_execution.config import Config
from yoyo.okx_execution.risk import D, advance, plan, MarketOrderLimitError, InsufficientMarginError


def inputs(side="long", timeframe="15m"):
    return (dict(symbol="ALT-USDT-SWAP", side=side, timeframe=timeframe, initial_stop=98 if side == "long" else 102),
            dict(instId="ALT-USDT-SWAP", instType="SWAP", ctType="linear", settleCcy="USDT", state="live",
                 ctVal="0.1", ctMult="100", lotSz="0.1", minSz="0.1", maxMktSz="10000", tickSz="0.01", lever="100"),
            dict(askPx="100", bidPx="100"),
            [dict(minSz="0", maxSz="100", maxLever="75"), dict(minSz="100", maxSz="1000", maxLever="25")])


def make_plan(side="long", timeframe="15m", **kwargs):
    options = dict(equity="125", available="125", allocated_margin="0", existing_size="0", config=Config())
    options.update(kwargs)
    return plan(*inputs(side, timeframe), **options)


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("timeframe", ["15m", "30m", "1H", "4H"])
def test_all_periods_directions_without_portfolio_caps(side, timeframe):
    result = make_plan(side, timeframe)
    assert D(result["size"]) == D("12.5")
    assert D(result["planned_risk"]) == D("2.5")
    assert D(result["notional"]) == 125  # ctMult is not multiplied twice.
    assert D(result["leverage"]) == 75
    assert Config().max_positions is None and Config().aggregate_risk_limit is None


def test_aggregate_same_side_changes_venue_tier_not_signal_eligibility():
    assert D(make_plan(existing_size="100")["leverage"]) == 25


def test_equity_loss_scales_risk_and_profit_does_not_expand_funding_allocation():
    assert D(make_plan(equity="25")["planned_risk"]) == D("0.5")
    assert D(make_plan(equity="50")["planned_risk"]) == 1
    assert D(make_plan(equity="100")["planned_risk"]) == 2
    assert D(make_plan(equity="200")["planned_risk"]) == D("2.5")
    with pytest.raises(ValueError, match="insufficient"):
        make_plan(allocated_margin="124.9")


def test_below_minimum_is_not_rounded_up():
    event, instrument, quote, tiers = inputs()
    instrument["minSz"] = "10"
    with pytest.raises(ValueError, match="minimum"):
        plan(event, instrument, quote, tiers, equity="50", available="50", allocated_margin="0", existing_size="0", config=Config())


def test_margin_rejection_explains_actual_max_leverage_and_shortfall():
    with pytest.raises(InsufficientMarginError, match='insufficient available allocation') as error:
        make_plan(available='1')
    d = error.value.details
    assert d['leverage'] == '75' and d['requested_size'] == '12.5'
    assert D(d['required_margin']) == D(125)/75
    assert D(d['usable_margin']) == 1
    assert D(d['shortfall']) == D(d['required_margin'])-1
    # Available equity already accounts for used margin: do not subtract twice.
    assert make_plan(available='2', allocated_margin='100')['size'] == '12.5'


def test_no_artificial_leverage_safety_filter():
    result = make_plan()
    assert D(result["leverage"]) == 75  # Stop at -2% is beyond a 75x initial-margin fraction.
    assert result["liquidation_can_precede_stop"] is True


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "", None, True])
def test_bad_numeric_values_fail(bad):
    with pytest.raises(ValueError):
        D(bad)


@pytest.mark.parametrize("side,close,expected", [("long", 105, "101"), ("short", 95, "99")])
def test_two_r_closed_arm_and_monotone_trail(side, close, expected):
    p = dict(side=side, timeframe="15m", entry_price="100", initial_stop="98" if side == "long" else "102",
             stop="98" if side == "long" else "102", tick="0.01")
    change = advance(p, dict(close=close, atr=1, close_ms=100, raw_side=0))
    assert change["trailing"] and D(change["stop"]) == D(expected)
    p.update(stop=change["stop"], trailing=True)
    worse = advance(p, dict(close=100, atr=3, close_ms=200, raw_side=0))
    assert D(worse["stop"]) == D(expected)
    p["timeframe"] = "1H"
    better = advance(p, dict(close=close, atr=1, close_ms=300, raw_side=-1 if side == "long" else 1))
    assert D(better["stop"]) == (103 if side == "long" else 97)
    assert better["exit"]


def test_only_owner_contract_supported():
    with pytest.raises(ValueError):
        replace(Config(), max_positions=3).validate()


def test_cross_leverage_is_shared_with_larger_opposite_position():
    result = make_plan(opposite_size="110")
    assert D(result["leverage"]) == 25
    assert D(result["planned_risk"]) == D("2.5")
    assert result["margin_mode"] == "cross"
    with pytest.raises(ValueError, match="opposite position tier"):
        make_plan(opposite_size="1001")


def test_previous_isolated_contract_cannot_start_as_cross():
    with pytest.raises(ValueError):
        replace(Config(), margin_mode="isolated").validate()


def test_beat_contract_limit_reports_risk_calculation_in_contracts():
    from yoyo.okx_execution.manual import Sizing
    event = dict(symbol='BEAT-USDT-SWAP', side='long', timeframe='5m', initial_stop='0.07691')
    instrument = dict(instId=event['symbol'], instType='SWAP', ctType='linear', settleCcy='USDT', state='live',
                      ctVal='10', ctMult='1', lotSz='0.1', minSz='0.1', maxMktSz='7000', tickSz='0.00001', lever='10')
    with pytest.raises(MarketOrderLimitError) as error:
        plan(event, instrument, dict(askPx='0.07697',bidPx='0.07696'), [], equity='157.4722',
             available='157.4722', allocated_margin='0', existing_size='0', config=Sizing('157.4722'))
    d = error.value.details
    assert d['requested_size'] == '7873.6' and d['max_market_size'] == '7000'
    assert d['target_risk'] == '4.724166' and d['stop_distance'] == '0.00006'
    assert d['contract_value'] == '10' and d['quantity_unit'] == 'contracts'


def test_exact_venue_maximum_is_allowed_without_unit_conversion():
    event, instrument, quote, tiers = inputs()
    instrument['maxMktSz'] = '12.5'
    result = plan(event, instrument, quote, tiers, equity='125', available='125', allocated_margin='0',
                  existing_size='0', config=Config())
    assert result['size'] == '12.5'
