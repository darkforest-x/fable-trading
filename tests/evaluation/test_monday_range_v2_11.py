"""Guard unchanged range/exit rules; native Pine fixtures exercise the clocks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "yoyo/evaluation/pine/monday_range_v2_10.pine").read_text()
NEW = (ROOT / "yoyo/evaluation/pine/monday_range_v2_11.pine").read_text()


def between(source, start, end):
    return source.split(start, 1)[1].split(end, 1)[0]


def test_stops_management_and_signal_definitions_preserved():
    assert between(OLD, "txBufferPrice(", "txPack(") == between(NEW, "txBufferPrice(", "txPack(")
    assert between(OLD, "int txStopBars", "// === Monday Range") == between(NEW, "int txStopBars", "// === Monday Range")
    assert between(OLD, "string timeGroup", "// === Fixed 1H signal event buffer").replace("h1LineReserve", "ray15LineReserve") == between(NEW, "string timeGroup", "// === Fixed 15m signal event buffer")


def test_observation_refresh_does_not_change_exit_clock_or_entry_order():
    engine = between(NEW, "txEngine() =>", "txConfirmed() =>")
    assert 'txRefreshObservation(watch, txEntryTf, hClose, hourSide, hourEnd, qClose, quarterSide, quarterEnd)' in engine
    assert engine.index("txRefreshObservation") < engine.index("for trade in live") < engine.index("if txEntryEligible")
    assert "if hClose\n            txExitOnH1(trade, hourSide, hourEnd, close)" in engine
    assert "close, qClose, quarterAtr, txArmR, txAtrMult" in engine
    assert 'string txEntryTf = timeframe.main_period' in NEW
    assert 'bool txChartSupported = txEntryTf == "5" or txEntryTf == "15"' in NEW


def test_ray_source_and_confirmation_are_fifteen_minute_on_every_chart():
    rays = between(NEW, "type QuarterRaySnapshot", "// === V2.2 fixed-timeframe")
    assert 'if timeframe.in_seconds() < 900' in rays
    assert '"60"' not in rays
    assert '"15", confirmedRay15Engine(), lookahead=barmerge.lookahead_on' in rays
    assert '"15", ray15EnginePair(), lookahead=barmerge.lookahead_off' in rays
    assert 'latestRay15.closeAt <= observedThrough' in rays
    assert 'if confirmedAt <= observedThrough' in rays
    assert 'color=direction == 1 ? ray15BullRayColor : ray15BearRayColor' in rays
    assert 'input.bool(true, "显示 15m 勾叉竖射线"' in NEW
    assert 'input.bool(false, "启用多周期观察交易参考"' in NEW


def test_new_private_version_has_no_test_appendix():
    assert 'indicator("周一高低点区间＋翻身 · V2.11"' in NEW
    assert 'V211_' not in NEW
    assert 'runtime.error("V211' not in NEW
