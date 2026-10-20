"""V2.7 armed-only hourly exit and compact R display regression.

Uses hand-computed causal OHLC paths plus registered PEPE replay/native evidence.
No account performance inference or new external data.
"""
from pathlib import Path
import json
import re
import pytest
from yoyo.evaluation.monday_range_v2_7_reference import Trade

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_6.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_7.pine').read_text()


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.parametrize('side', [1, -1])
def test_same_close_arming_then_reverse_exit(side):
    t = Trade(1000, 0, side, 100, 100-side, 100-side, tick=.01)
    t.advance(1000, 2000, 100, 106 if side == 1 else 100.5,
              99.5 if side == 1 else 94, 100+5*side, 1, -side)
    assert (t.armed_at, t.ended, t.reason, t.current_r, t.peak_r) == (2000, 2000, 3, 5, 6)
    t.advance(2000, 3000, 105, 200, 0, 180, 1, -side)
    assert t.ended == 2000 and t.exit_price == 100 + 5*side


def test_armed_and_unarmed_peers_are_independent():
    armed = Trade(1000, 0, 1, 100, 99, 99, tick=.01)
    armed.advance(1000, 2000, 100, 106, 99.5, 105, 1)
    peer = Trade(2000, 1000, 1, 105, 95, 95, tick=.01)
    for t in (armed, peer):
        t.advance(2000, 3000, 105, 107, 104, 106, 1, -1)
    assert armed.reason == 3 and armed.exit_price == 106
    assert peer.ended == 0 and peer.armed_at == 0


@pytest.mark.parametrize('hour_side', [0, 1])
def test_non_reverse_event_keeps_tracking(hour_side):
    t = Trade(1000, 0, 1, 100, 99, 99, tick=.01)
    t.advance(1000, 2000, 100, 106, 99.5, 105, 1, hour_side)
    assert t.armed_at == 2000 and t.ended == 0


def test_stop_wins_and_ambiguous_stop_bar_does_not_inflate_peak():
    t = Trade(1000, 0, 1, 100, 99, 99, tick=.01)
    t.advance(1000, 2000, 100, 106, 99.5, 105, 1)
    t.advance(2000, 3000, 105, 110, 100, 109, 1, -1)
    assert (t.reason, t.exit_price, t.current_r, t.peak_r) == (2, 101, 1, 6)


def test_armed_reverse_exit_has_no_hidden_profit_filter():
    t = Trade(1000, 0, 1, 100, 99, 99, tick=.01)
    t.advance(1000, 2000, 100, 106, 99.5, 105, 10)
    t.advance(2000, 3000, 105, 106, 99.1, 99.5, 10, -1)
    assert t.reason == 3 and t.current_r == -.5


def test_previous_rules_are_preserved_except_confirmed_hour_exit():
    old = between(OLD, 'txAdvance(', 'txSignals() =>')
    assert NEW.split('txAdvance(', 1)[1].startswith(old)
    old_engine = between(OLD, 'txEngine() =>', 'txConfirmed() =>')
    new_engine = between(NEW, 'txEngine() =>', 'txConfirmed() =>')
    assert old_engine == new_engine.replace('        if hClose\n            txExitOnH1(trade, hourSide, hourEnd, close)\n', '')
    for start, end in [('txObserve(', 'txAdvance('), ('txSignals() =>', 'txEngine() =>'),
                       ('txConfirmed() =>', '// The drawing layer'),
                       ('// These globals trigger', 'if barstate.islast\n    for item in txBoxes')]:
        assert between(OLD, start, end) == between(NEW, start, end)
    assert 'input.bool(false, "交易框显示价格"' in NEW
    assert ' · 最大 ' in NEW and 'keyInfo' not in NEW and 'showKeyInfo' not in NEW
    assert '7 * txKeep' in NEW and 'input.bool(false, "启用 1H→15m' in NEW
    draw = between(NEW, 'txDraw(string row', '// These globals trigger')
    for line in draw.splitlines():
        if 'txText(' in line:
            assert 'format.mintick' not in line


def test_recent_admissions_unchanged_and_expected_three_exits():
    old = json.loads((ROOT/'output/pine/monday_range_v2_5_reference.json').read_text())
    new = json.loads((ROOT/'output/pine/monday_range_v2_7_reference.json').read_text())
    keys = ('entered', 'observed', 'side', 'entry', 'initial_stop')
    assert [{k:t[k] for k in keys} for t in old['trades']] == [{k:t[k] for k in keys} for t in new['trades']]
    assert len(new['trades']) == 10
    reverse = [t for t in new['trades'] if t['reason'] == 3]
    assert [t['ended'] for t in reverse] == [1792249200000, 1792249200000, 1792375200000]
    assert [t['current_r'] for t in reverse] == pytest.approx([7.709677419354778, 7.461538461538361, 14.941176470588603])


def test_native_closed_records_match_independent_ohlc_replay():
    reference = json.loads((ROOT/'output/pine/monday_range_v2_7_reference.json').read_text())
    log = (ROOT/'output/pine/monday_range_v2_7_native_15m.txt').read_text()
    assert 'V27_NATIVE_CHECKS_PASS' in log.replace('V27_NATIVE_ CHECKS_PASS', 'V27_NATIVE_CHECKS_PASS')
    rows = {int(r.split(',')[0]): r.split(',') for r in re.findall(r'V27_PARITY_ROW\s+(\d[^\n]+)', log)}
    checked = []
    keys = ('entered', 'observed', 'side', 'entry', 'initial_stop', 'protection', 'peak_r', 'current_r', 'armed_at', 'ended', 'exit_price', 'reason')
    for trade in reference['trades']:
        if not trade['ended']:
            continue
        fields = rows[trade['entered']]
        for i,k in enumerate(keys):
            value = float(fields[i])
            if k in ('entry', 'initial_stop', 'protection', 'exit_price'):
                assert abs((value-trade[k])/1e-9) < 1e-6, (trade['entered'], k)
            else:
                assert value == pytest.approx(trade[k], abs=1e-6, rel=0), (trade['entered'], k)
        checked.append(trade['entered'])
    assert len(checked) == 10


def test_native_chart_timeframes_have_identical_trade_ledger():
    logs = [(ROOT/f'output/pine/monday_range_v2_7_native_{tf}.txt').read_text() for tf in ('15m', '1h')]
    signatures = [re.search(r'timeSig=[^\n]+', text).group() for text in logs]
    assert signatures[0] == signatures[1]
    assert all('count=146 active=0 total=146' in text for text in logs)


def test_range_and_fanshen_preserved_except_removed_status_table():
    old = between(OLD, 'string timeGroup =', '// === V2.2 fixed-timeframe')
    old = '\n'.join(line for line in old.split('\n') if not line.startswith('bool showKeyInfo =') and not line.startswith('var table keyInfo ='))
    start = old.index('    if showKeyInfo\n')
    end = old.index('// Data Window exposes', start)
    old = old[:start] + '\n' + old[end:]
    assert old == between(NEW, 'string timeGroup =', '// === V2.2 fixed-timeframe')
