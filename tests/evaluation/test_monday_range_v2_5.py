"""V2.5 preservation checks; native fixtures exercise the Pine state helpers."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "yoyo/evaluation/pine/monday_range_v2_4.pine").read_text()
NEW = (ROOT / "yoyo/evaluation/pine/monday_range_v2_5.pine").read_text()


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_range_fanshen_rays_and_alerts_are_unchanged():
    for source in (OLD, NEW):
        assert 'input.bool(false, "启用 1H→15m 交易参考"' in source
        assert 'input.float(0.05, "结构极值外缓冲值"' in source
    start, end = 'string timeGroup =', '// === V2.2 fixed-timeframe independent trade engine'
    assert between(OLD, start, end) == between(NEW, start, end).replace('V2.5', 'V2.4')


def test_only_admission_changes_not_stops_paths_retention_or_rendering():
    for start, end in [('txBufferPrice(', 'txEngine() =>'),
                       ('txConfirmed() =>', 'if barstate.islast\n    for item in txBoxes')]:
        assert between(OLD, start, end) == between(NEW, start, end)
    old_engine = between(OLD, 'txEngine() =>', 'txConfirmed() =>')
    old_engine = old_engine.replace('        watch.consumed := true\n', '').replace('watch.consumed, total,', 'total,')
    assert old_engine == between(NEW, 'txEngine() =>', 'txConfirmed() =>')
    assert 'not watch.consumed' not in NEW
    assert 'hSide != watch.side' not in NEW
    assert 'qAt > watch.since' in NEW
    assert 'if hSide != 0 and hAt > watch.since' in NEW


def test_live_tracking_and_display_capacity_are_separate():
    assert between(OLD, 'txPrune(', 'txEngine() =>') == between(NEW, 'txPrune(', 'txEngine() =>')
    assert 'if trades.get(index).endedAt != 0' in NEW
    assert 'for trade in trades\n        txAdvance' in NEW
    assert 'max_boxes_count=100, max_polylines_count=40' in NEW
    assert 'input.int(10, "显示最近交易框数", minval=1, maxval=20' in NEW


def test_captured_native_paths_match_independent_ohlc_replay():
    from yoyo.evaluation.monday_range_v2_5_reference import replay
    result = replay()
    native = (ROOT / 'output/pine/monday_range_v2_5_native_15m.txt').read_text()
    rows = {int(fields[0]): fields for fields in
            (row.split(',') for row in re.findall(r'(?:TX_ROW|V25_AUDIT_ROW)\s+(\d[^\n]+)', native))}
    keys = ['entered', 'observed', 'side', 'entry', 'initial_stop', 'protection',
            'peak_r', 'current_r', 'armed_at', 'ended', 'exit_price', 'reason']
    checked = []
    for trade in result['trades']:
        if trade['entered'] not in rows:
            continue
        # Live MTM may move after the saved OHLC input boundary.
        selected = keys if trade['ended'] else keys[:5]
        for index, key in enumerate(selected):
            error = float(rows[trade['entered']][index]) - trade[key]
            scale = 1e-9 if key in ['entry', 'initial_stop', 'protection', 'exit_price'] else 1
            assert abs(error / scale) < 1e-6, (trade['entered'], key)
        checked.append((trade['entered'], len(selected)))
    assert (1792128600000, 12) in checked  # Owner's formerly missing short.
    assert len(checked) >= 7
    assert result['maximum_entries_per_h1_observation'] >= 3
    assert result['max_simultaneous'] >= 2
    one_hour = (ROOT / 'output/pine/monday_range_v2_5_native_1h.txt').read_text()
    signature = r'count=10 active=1 total=146.*sideSig=-25'
    assert re.search(signature, native).group() == re.search(signature, one_hour).group()
