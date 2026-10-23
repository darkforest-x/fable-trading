"""Delivery guards; numerical state fixtures execute the real Pine in TradingView."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_38.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_39.pine').read_text()
START = '// === V2.39 grouped 15-symbol scanner'
END = 'int rangeVisibleLeft = chart.left_visible_bar_time'
BLOCK = NEW[NEW.index(START):NEW.index(END)]


def test_original_indicator_unchanged():
    restored = NEW[:NEW.index(START)] + NEW[NEW.index(END):]
    assert restored.replace('· V2.39", shorttitle="区间翻身 V2.39"', '· V2.38", shorttitle="区间翻身 V2.38"') == OLD


def test_two_disjoint_groups_have_thirty_unique_okx_contracts():
    groups = re.findall(r'input.text_area\("([^"]+)"', BLOCK)
    assert len(groups) == 2
    symbols = [symbol for group in groups for symbol in group.split(',')]
    assert all(len(group.split(',')) == 15 for group in groups)
    assert len(set(symbols)) == 30
    assert all(re.fullmatch(r'OKX:[A-Z0-9]+USDT\.P', symbol) for symbol in symbols)


def test_scanner_uses_full_monday_remote_ohlc_with_bounded_requests():
    for contract in ['sourceAt == start', 'bars == 96', 'sourceAt - previousAt == 900000', 'sourceAt < expires', 'timeframe.in_seconds() != 900', 'not chart.is_standard', 'calc_bars_count=2000', 'ignore_invalid_symbol=true', 'lookahead=barmerge.lookahead_off']:
        assert contract in BLOCK
    assert BLOCK.count('request.security(') == 1
    assert 'for i = 0 to 14' in BLOCK


def test_realtime_baseline_dedup_queue_and_rate_limit():
    assert 'varip array<bool> scanPrimed' in BLOCK
    assert 'primed and not na(touchAt)' in BLOCK
    assert 'scanPrimed.set(i, false)' in BLOCK
    assert 'varip string scanPending' in BLOCK
    assert 'timenow - scanLastSent >= 15000' in BLOCK
    assert 'alert.freq_all' in BLOCK
    assert 'V239_FIXTURES' not in NEW
