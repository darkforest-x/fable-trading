"""Protect V2.37 behavior while adding a separate confirmed-wick display.

Executable price/geometry boundary fixtures run in TradingView's Pine runtime;
this local contract check guards the much larger untouched baseline modules.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_37.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_40.pine').read_text()


def test_baseline_modules_are_preserved():
    anchor = 'int rangeVisibleLeft = chart.left_visible_bar_time'
    tail = NEW[NEW.index(anchor):].replace(' - wick1618Capacity', '')
    assert tail == OLD[OLD.index(anchor):]
    start = 'type MondayRange'
    end = "// Leave other modules' peak label reservations intact even when all are enabled."
    assert NEW[NEW.index(start):NEW.index(end)] == OLD[OLD.index(start):OLD.index(anchor)]


def test_rings_reserve_label_capacity_and_reject_forming_bars():
    assert 'max_polylines_count=40)' in NEW
    assert 'wfLabelReserve - wick1618Capacity' in NEW
    assert 'if showWick1618 and wick1618Capacity > 0 and barstate.isconfirmed and wick1618Visible' in NEW
    code = NEW.split('wick1618Draw(', 1)[1].split('var array<label> wick1618Rings', 1)[0]
    assert code.index('label.delete(rings.shift())') < code.index('label.new(')
    assert 'rings.size() >= wick1618Capacity' in code
    assert 'time < wick1618Expires' in NEW


def test_owner_observed_defaults():
    for setting in [
        'showWick1618 = input.bool(true,',
        'wick1618Color = input.color(#00B43B,',
        'showLabels = input.bool(false,',
        'histFibLevelsStr = input.string("1.618",',
        'histFibCol = input.color(#FFFFFF,',
    ]:
        assert setting in NEW
    assert 'V240_FIXTURE' not in NEW
