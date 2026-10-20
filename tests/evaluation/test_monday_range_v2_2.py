"""Source preservation/capacity contracts; native Pine fixtures test behavior.

The behavioral companion invokes production observation/path helpers on
hand-computed bars. This file does not pretend to execute Pine in Python.
"""
from pathlib import Path
import re
ROOT = Path(__file__).resolve().parents[2]
PINE = ROOT / 'yoyo/evaluation/pine'
BASE = (PINE / 'monday_range_v2_1.pine').read_text()
SOURCE = (PINE / 'monday_range_v2_2.pine').read_text()
ENGINE = '// === V2.2 fixed-timeframe independent trade engine'


def test_existing_range_fanshen_and_hourly_engines_preserved():
    start = 'string timeGroup ='
    existing = SOURCE[SOURCE.index(start):].split(ENGINE)[0].rstrip()
    existing = existing.replace('V2.2', 'V2.1').replace('math.max(h1LineReserve, txLabelReserve)', 'h1LineReserve')
    existing = existing.replace('calc_bars_count=txEnabled ? 12000 : 3200', 'calc_bars_count=3200')
    existing = existing.replace('// Enabled frames need 10000 completed 15m bars plus warmup for 30000 x 5m.\n// Native validation found the earlier 3200-bar 15m request capped shared\n// coverage, even when the later nested request asked for more history.\n', '')
    assert existing == BASE[BASE.index(start):].rstrip()


def test_default_off_and_owner_parameters():
    assert 'input.bool(false, "启用 1H→15m 交易参考"' in SOURCE
    for signature in ['input.int(5, "止损', 'input.float(1.0, "结构', 'input.float(5.0, "15m收盘', 'input.int(14, "15m ATR', 'input.float(4.0, "15m跟踪']:
        assert signature in SOURCE
    assert 'strategy(' not in SOURCE


def test_confirmed_fixed_source_and_no_entry_bar_extrema():
    engine = SOURCE.split(ENGINE)[1]
    assert '"5", txConfirmed(), lookahead=barmerge.lookahead_on' in engine
    assert '"5", txPair(), lookahead=barmerge.lookahead_off' in engine
    assert 'currentTx.closeAt <= h1ObservedAt ? currentTx : priorTx' in engine
    assert 'bool hClose = time_close == hourEnd' in engine
    assert 'bool qClose = time_close == quarterEnd' in engine
    assert 'at >= trade.enteredAt' in engine
    assert 'qAt > watch.since' in engine
    assert 'not na(low[txStopBars - 1])' in engine
    assert 'bar_index' not in engine
    assert 'hSide != watch.side' in engine
    assert engine.index('bool stopped =') < engine.index('trade.armed := true')
    assert 'math.max(trade.protection, candidate)' in engine
    assert 'math.min(trade.protection, candidate)' in engine
    assert not re.search(r'(?<![a-zA-Z_])line\.new\(', engine)


def test_line_label_box_and_polyline_capacity():
    assert 'max_boxes_count=100, max_polylines_count=40' in SOURCE
    assert '7 * txKeep' in SOURCE
    # Fib lines and labels share retention; reserve the larger of the two
    # independent drawing pools, not their sum. At least one 24-line week fits.
    for weeks in range(1, 66):
        for rays in (0, *range(1, 201)):
            for frames in (0, *range(1, 21)):
                shared = 500 - 2 * weeks - 24 - 19 - 2 - max(rays, 7 * frames)
                assert shared >= 24
                assert shared + 2 * weeks + 24 + 19 + rays <= 498
                assert shared + 2 * weeks + 24 + 19 + 7 * frames <= 498
                assert 5 * frames <= 100 and frames <= 40


def test_oracle_requires_complete_candles_and_does_not_read_entry_wicks():
    import pandas as pd
    from yoyo.evaluation.monday_range_v2_2_reference import aggregate, Trade
    minute = pd.DataFrame({'open': [100]*5, 'high': [101]*5, 'low': [99]*5, 'close': [100]*5}, index=pd.date_range('2026-01-01', periods=5, freq='min', tz='UTC'))
    assert aggregate(minute.iloc[:4], 5).empty
    complete = aggregate(minute, 5)
    assert list(complete.index) == [pd.Timestamp('2026-01-01 00:05', tz='UTC')]
    trade = Trade(1000, 0, 1, 100, 99, 99)
    trade.advance(0, 1000, 100, 120, 1, 105, 1)
    assert trade.ended == trade.armed_at == 0 and trade.peak_r == 0


def test_oracle_close_trigger_old_stop_priority_and_independence():
    from yoyo.evaluation.monday_range_v2_2_reference import Trade
    long = Trade(1000, 0, 1, 100, 99, 99)
    short = Trade(2000, 1000, -1, 105, 110, 110)
    long.advance(1000, 2000, 100, 106, 99.5, 104, 1)
    assert not long.armed_at  # A wick reaching 6R cannot arm the trail.
    long.advance(2000, 3000, 104, 106, 100, 105, 1)
    assert long.protection == 101 and long.armed_at == 3000
    long.advance(3000, 4000, 105, 110, 100.5, 109, 1)
    short.advance(3000, 4000, 105, 109, 100.5, 109, 1)
    assert long.ended == 4000 and long.exit_price == 101
    assert short.ended == 0  # Another reference's exit cannot close this one.
