"""Tests for the original-to-V9 Pine migration ledger."""
from scripts.audit_pine_eth_15m_migration import migration_checks


def test_migration_checks_require_both_old_defect_and_new_contract() -> None:
    original = """//@version=5
strategy("old", calc_on_every_tick=true)
ratio = perc_r != 0 ? diff / perc_r : 0
current_hour = hour
float pos_qty = 400
morning_boost_mult = 1.5
thursday_boost_mult = 2.0
sl_price := close - final_sl_dist
sl_price := close + final_sl_dist
strategy.close("Long")
strategy.close("Short")
"""
    v9 = """//@version=6
strategy("new", calc_on_every_tick = false, commission_value = 0.10, slippage = 0, use_bar_magnifier = false, pyramiding = 0)
float targetQuantity = strategy.equity * targetLeverage / close
const float RISK_PER_TRADE_PERCENT = 1.0
stopPrice := strategy.position_avg_price - nz(pendingLongStopTicks, signalStopTicks)
stopPrice := strategy.position_avg_price + nz(pendingShortStopTicks, signalStopTicks)
int hourHk = hour(time, "Asia/Hong_Kong")
if timeframe.in_seconds() != 900
if syminfo.basecurrency != "ETH"
int researchStart = 1
int researchEnd = 2
bool percentileSafe = not na(percentile99) and percentile99 > 0.0
RESEARCH ONLY RESEARCH ONLY RESEARCH ONLY RESEARCH ONLY RESEARCH ONLY
"""
    checks = migration_checks(original, v9)
    assert checks
    assert all(checks.values())


def test_migration_checks_fail_if_commission_is_still_implicit() -> None:
    checks = migration_checks("//@version=5\ncommission_value = 0", "//@version=6\n")
    assert checks["commission_made_explicit"] is False
