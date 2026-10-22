"""Preserve the15m lane and exits while adding explicit retrospective5m review.

Native fixtures exercise chronological publication and pre-confirmation outcomes.
Source guards prevent this display change from altering unrelated signal modules.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_33.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_36.pine').read_text()


def section(s, first, last):
    return s.split(first, 1)[1].split(last, 1)[0]


def test_original_fifteen_minute_engine_is_identical():
    assert section(OLD, 'txEngine() =>', 'txConfirmed() =>').strip() == section(
        NEW, 'txEngine() =>', '// A review candidate').strip()
    assert 'txEntryTf == "5" ? txReviewEngine() : txEngine()' in NEW


def test_key_module_unchanged():
    assert OLD[OLD.index('type WfTrade'):] == NEW[NEW.index('type WfTrade'):]


def test_stop_exit_and_raw_signal_calculation_unchanged():
    assert section(OLD, 'txBufferPrice(', 'txPack(') == section(NEW, 'txBufferPrice(', 'txPack(')
    assert section(OLD, 'fs_periodK =', '// === Fixed 15m signal event buffer') == section(
        NEW, 'fs_periodK =', '// === Fixed 15m signal event buffer').replace(' and not txReviewMode', '')


def test_native_test_code_is_not_delivered():
    assert 'shorttitle="区间翻身 V2.36"' in NEW
    assert 'V236_FIXTURE' not in NEW
    assert 'TxPending' not in NEW
    assert 'txMatchWindow = input.int(5' in NEW


def test_window_uses_candle_positions_and_records_confirmation():
    assert 'math.abs(fiveAt - quarterAt) <= window * 300000' in NEW
    assert 'TxReviewQuarter.new(quarterEnd - 900000, quarterEnd, quarterSide)' in NEW
    assert 'candidate.confirmedAt := math.max(candidate.trade.enteredAt, quarter.closeAt)' in NEW
    assert 'quarter.closeAt <= nowAt' in NEW


def test_pending_outcomes_advance_before_visibility_matching():
    engine = section(NEW, 'txReviewEngine() =>', 'txConfirmed() =>')
    assert engine.index('for candidate in pending') < engine.index('if fiveSide != 0') < engine.index('txMatchReview(')
    assert 'txAdvance(candidate.trade, time, time_close' in engine
    assert 'txExitOnH1(candidate.trade' in engine
    assert 'txEntryEligible' not in engine
