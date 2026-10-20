"""Viewport display regression: preserve V2.5 trade rules and verify native evidence.

Inputs are saved TradingView logs and the prior OHLC replay. These tests do not
infer profitability or enlarge the research data window.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_5.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_6.pine').read_text()


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_numerical_trade_rules_and_admission_are_unchanged():
    for start, end in [('txObserve(', '// Closed records remain'),
                       ('txConfirmed() =>', '// The drawing layer')]:
        old_end = '// Keep all live references' if end == '// Closed records remain' else end
        assert between(OLD, start, old_end) == between(NEW, start, end)
    for start, end in [('    [hourEnd,', '    var TxWatch'),
                       ('    if qClose and txEligible', '            trades.push')]:
        actual = between(NEW, start, end).replace(
            '            TxTrade entered = TxTrade.new(time_close, watch.since, watch.side, close, initial, risk, initial)\n', '')
        assert between(OLD, start, end) == actual
    assert 'for trade in live\n        txAdvance' in NEW
    assert 'trades.push(entered)\n            live.push(entered)' in NEW
    assert 'trades.remove(' not in NEW
    assert 'chart.left_visible' not in between(NEW, 'txEngine() =>', 'txConfirmed() =>')
    assert 'chart.right_visible' not in between(NEW, 'txEngine() =>', 'txConfirmed() =>')
    assert 'txKeep' not in between(NEW, 'txEngine() =>', 'txConfirmed() =>')


def test_other_features_and_frame_geometry_are_preserved():
    assert between(OLD, 'string timeGroup =', '// === V2.2 fixed-timeframe') == between(
        NEW, 'string timeGroup =', '// === V2.2 fixed-timeframe').replace('V2.6', 'V2.5')
    old_draw = between(OLD, 'txText(', 'if barstate.islast\n    for item in txBoxes')
    new_draw = between(NEW, 'txText(', '// These globals trigger')
    assert old_draw == new_draw.replace('txFinish(entered, ended, confirmedThrough)',
        'math.max(entered + 300000, live ? confirmedThrough + 900000 : ended)')
    for text in ['input.bool(false, "启用 1H→15m 交易参考"',
                 'input.float(0.05, "结构极值外缓冲值"',
                 'if time == txVisibleRight\n    txVisibleEnd := time_close']:
        assert text in NEW


def test_render_capacity_is_bounded_and_disclosed():
    assert 'input.int(40, "可见时段最多显示交易框数", minval=1, maxval=40' in NEW
    assert 'max_boxes_count=200, max_polylines_count=40' in NEW
    assert '7 * txKeep' in NEW
    # Worst settings retain at least the latest 24 Fib labels after reservations.
    assert 500 - 2 * 65 - 24 - 19 - 2 - max(200, 7 * 40) >= 24
    assert '请缩小时间范围' in NEW and '更早无5m数据' in NEW
    assert 'array<string> trades' in NEW


def parse_rows(text, marker):
    return {int(row.split(',')[0]): row.split(',') for row in
            re.findall(marker + r'\s+(\d[^\n]+)', text)}


def test_native_trade_paths_match_prior_version():
    old = (ROOT / 'output/pine/monday_range_v2_5_native_15m.txt').read_text()
    new = (ROOT / 'output/pine/monday_range_v2_6_native_recent.txt').read_text()
    old_rows = parse_rows(old, '(?:TX_ROW|V25_AUDIT_ROW)')
    new_rows = parse_rows(new, 'V26_PARITY_ROW')
    checked = []
    for entered, old_fields in old_rows.items():
        if entered not in new_rows or int(old_fields[9]) == 0:
            continue
        assert old_fields[:12] == new_rows[entered][:12], entered
        checked.append(entered)
    assert 1792128600000 in checked
    assert len(checked) >= 6


def test_native_viewport_membership_and_history_navigation():
    seen = []
    for name in ('recent', 'older', 'shifted', 'wide'):
        text = (ROOT / f'output/pine/monday_range_v2_6_native_{name}.txt').read_text()
        assert 'V26_NATIVE_CHECKS_PASS' in text.replace('V26_NATIVE_ CHECKS_PASS', 'V26_NATIVE_CHECKS_PASS')
        view = re.search(r'V26_VIEW left=([\d,]+) right=([\d,]+) rightEnd=([\d,]+) matched=([\d,]+) shown=([\d,]+) boxes=([\d,]+) labels=([\d,]+) polys=([\d,]+)', text)
        assert view, name
        left, right, end, matched, shown, boxes, labels, polys = [int(x.replace(',', '')) for x in view.groups()]
        assert right < end
        assert shown == min(40, matched)
        assert boxes <= 200 and labels <= 280 and polys <= 40
        rows = parse_rows(text, 'V26_VISIBLE_ROW')
        if name == 'wide':
            assert matched > 40 and boxes == 200
            assert len(rows) > 0  # The UI virtualizes long log lists.
        else:
            assert len(rows) == shown
        through = int(re.search(r'through=([\d,]+)', text).group(1).replace(',', ''))
        for entry, fields in rows.items():
            exited = int(fields[9])
            finish = max(entry + 300000, exited or through + 900000)
            assert entry < end and finish >= left
        seen.append((left, right, rows))
    assert seen[1][0] < 1791100800000  # At least earlier than October 5.
    assert seen[1][0] != seen[2][0]  # Date navigation changed the viewport by twelve hours.
    assert set(seen[1][2]) != set(seen[0][2])
    assert set(seen[1][2]) & set(seen[2][2])  # Overlapping viewports keep the same records.


def test_archived_records_are_identical_in_overlapping_native_viewports():
    older = parse_rows((ROOT / 'output/pine/monday_range_v2_6_native_older.txt').read_text(), 'V26_VISIBLE_ROW')
    shifted = parse_rows((ROOT / 'output/pine/monday_range_v2_6_native_shifted.txt').read_text(), 'V26_VISIBLE_ROW')
    common = older.keys() & shifted.keys()
    assert len(common) == 3
    for entry in common:
        assert older[entry] == shifted[entry]


def test_viewport_selection_does_not_change_native_ledger():
    signatures = []
    for name in ('recent', 'older', 'shifted', 'wide'):
        text = (ROOT / f'output/pine/monday_range_v2_6_native_{name}.txt').read_text()
        assert 'count=146 active=1 total=146' in text
        signatures.append(re.search(r'timeSig=[^\n]+', text).group())
    assert len(set(signatures)) == 1
