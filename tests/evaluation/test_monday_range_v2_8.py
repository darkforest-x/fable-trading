"""V2.8 performance change boundaries and native regression evidence.

The full trade engine, history windows, and chart selection remain unchanged.
Native checks exercise cache equality and unchanged-drawing reuse in Pine.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT/'yoyo/evaluation/pine/monday_range_v2_7.pine').read_text()
NEW = (ROOT/'yoyo/evaluation/pine/monday_range_v2_8.pine').read_text()


def section(source, begin, end):
    return source.split(begin, 1)[1].split(end, 1)[0]


def test_entire_trade_engine_and_history_coverage_unchanged():
    assert section(OLD, 'type TxTrade', '// The drawing layer') == section(NEW, 'type TxTrade', '// The drawing layer')
    assert re.findall(r'calc_bars_count=[^\n]+', OLD) == re.findall(r'calc_bars_count=[^\n]+', NEW)
    assert section(OLD, 'string txGroup', '// === Monday Range') == section(NEW, 'string txGroup', '// === Monday Range')


def test_range_math_and_signals_unchanged():
    assert section(OLD, 'string timeGroup', 'keyEngine(int sourceTime') == section(NEW, 'string timeGroup', 'keyEngine(int sourceTime')
    assert section(OLD, '// HTF-on-LTF:', 'type TxTrade') == section(NEW, '// HTF-on-LTF:', 'type TxTrade')
    old = section(OLD, 'keyEngine(int sourceTime', '// HTF-on-LTF:')
    new = section(NEW, 'keyEngine(int sourceTime', '// HTF-on-LTF:')
    old = old[:old.index('    string packed = ""')] + old[old.index('    KeySnapshot.new'):]
    new = new[:new.index('    if nakedChanged\n')] + new[new.index('    KeySnapshot.new'):]
    new = '\n'.join(line for line in new.split('\n') if 'nakedChanged' not in line and 'var string packed' not in line)
    assert old == new


def test_native_reuse_and_ledger_parity():
    baseline = (ROOT/'output/pine/monday_range_v2_7_native_15m.txt').read_text()
    signature = re.search(r'timeSig=[^\n]+', baseline).group()
    for tf in ('15m', '1h'):
        log = (ROOT/f'output/pine/monday_range_v2_8_native_{tf}.txt').read_text()
        assert 'V28_NATIVE_CHECKS_PASS' in re.sub(r'\s+', '', log)
        assert 'V28_REUSE_PASS' in re.sub(r'\s+', '', log)
        assert re.search(r'timeSig=[^\n]+', log).group() == signature
    log = (ROOT/'output/pine/monday_range_v2_8_native_15m.txt').read_text()
    assert 'V28_KEY_CACHE_PARITY_PASS' in re.sub(r'\s+', '', log)
