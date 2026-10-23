"""Guard that touch alerts do not change V2.37 range or trade semantics.

Numerical transition fixtures run in TradingView against the actual Pine
function; this local contract checks the delivered production surface.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_37.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_38.pine').read_text()
START = '// === V2.38 Monday 1.618 touch alerts'
END = 'int rangeVisibleLeft = chart.left_visible_bar_time'


def test_original_range_and_trading_modules_unchanged():
    restored = NEW[:NEW.index(START)] + NEW[NEW.index(END):]
    restored = restored.replace('// V2.38: intrabar Monday 1.618 alerts; weekly side deduplication is independent of drawings.\n', '')
    restored = restored.replace('· V2.38", shorttitle="区间翻身 V2.38"', '· V2.37", shorttitle="区间翻身 V2.37"')
    assert restored == OLD


def test_intrabar_touch_is_independent_of_drawing_and_enabled_flags():
    block = NEW[NEW.index(START):NEW.index(END)]
    assert 'barstate.isconfirmed' not in block
    assert 'barstate.isrealtime' not in block  # History must consume first touches.
    assert 'showFib' not in block and 'wfWicksEnabled' not in block
    assert 'fibAlertSeenUp := fibAlertNextUp' in block
    assert 'fibAlertSeenDown := fibAlertNextDown' in block
    assert 'not barstate.isfirst' in block
    assert 'not isMonday' in block and 'time < fibAlertExpires' in block
    assert 'alert(' not in block  # Existing oscillator alerts stay independently selectable.


def test_three_conditions_and_symbol_specific_prices():
    block = NEW[NEW.index(START):NEW.index(END)]
    assert block.count('alertcondition(') == 3
    for value in ['任一侧触及', '上方触及', '下方触及', '{{ticker}}', '{{exchange}}', 'Monday 1.618 Upper', 'Monday 1.618 Lower']:
        assert value in block
    assert 'V238_FIXTURES' not in NEW
