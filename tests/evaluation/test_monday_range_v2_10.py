"""Protect prior engine semantics; native fixture verifies chart routing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT/"yoyo/evaluation/pine/monday_range_v2_9.pine").read_text()
NEW = (ROOT/"yoyo/evaluation/pine/monday_range_v2_10.pine").read_text()


def test_engine_stops_management_signals_and_drawings_unchanged():
    assert OLD.split("int txStopBars", 1)[1] == NEW.split("int txStopBars", 1)[1]


def test_chart_origin_is_not_rebound_inside_fixed_five_minute_request():
    assert "string txEntryTf = timeframe.main_period" in NEW
    assert 'bool txChartSupported = txEntryTf == "5" or txEntryTf == "15"' in NEW
    assert "bool txEnabled = txRequested and txChartSupported" in NEW
    assert 'input.bool(false, "启用 1H 观察交易参考"' in NEW
    assert "入场信号周期（分钟）" not in NEW
    assert "txEntryTf = input." not in NEW


def test_new_private_version_and_no_test_appendix():
    assert 'indicator("周一高低点区间＋翻身 · V2.10"' in NEW
    assert "V210_ROUTE_PASS" not in NEW
    assert "V210_CHART_ROUTE_FAIL" not in NEW
