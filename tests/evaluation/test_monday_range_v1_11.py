"""Source safeguards for the v1.8-based key-level display extension.

Native numerical/state-machine tests are in monday_range_v1_11_native_checks.pine.
Append that fixture to the source and run it in TradingView; these Python checks
do not claim to interpret Pine or replace its compiler.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'yoyo/evaluation/pine/monday_range_v1_11.pine'


def test_version_and_v18_fibonacci_prices_remain():
    source = SOURCE.read_text()
    parent = SOURCE.with_name('monday_range_v1_8.pine').read_text()
    for formula in ('lo + r * rng', 'hi - r * rng', 'monLow + r * rng', 'monHigh - r * rng'):
        assert formula in source and formula in parent
    for setting in ('"0.5, 0.618, 0.705, 1.13, 1.27, 1.618"', '"0.5, 0.618, 0.705"'):
        assert setting in source and setting in parent
    assert 'shorttitle="Monday Range v1.11"' in source
    assert 'shorttitle="Monday Range v1.8"' in parent
    assert 'showMini' not in source


def test_requests_keep_closed_snapshots_and_fixed_source_timeframe():
    source = SOURCE.read_text()
    assert re.search(r'confirmedKeyEngine\(\) =>\n    KeySnapshot value = keyEngine\([^\n]+\)\n    value\[1\]', source)
    assert '"15", confirmedKeyEngine(), lookahead=barmerge.lookahead_on' in source
    assert '"15", keyEnginePair(), lookahead=barmerge.lookahead_off' in source
    assert 'latestKey.closeAt <= observedAt ? latestKey : previousKey' in source
    assert source.count('request.security(') == 2


def test_new_levels_start_when_available_and_cannot_submit_orders():
    source = SOURCE.read_text()
    assert 'line.new(availableAt, price' in source
    assert 'drawKey(key.poc, key.profileEnd' in source
    assert 'drawKey(key.weekHigh, key.weekEnd' in source
    assert 'drawKey(price, sourceEnd' in source
    code = '\n'.join(line.split('//')[0] for line in source.splitlines())
    assert not re.search(r'\bstrategy\.|\balert\(|\balertcondition\(', code)


def test_custom_fib_and_key_levels_stay_inside_drawing_budget():
    source = SOURCE.read_text()
    assert source.count('if items.size() > 12') == 2
    assert '500 - 2 * keepWeeks - 24 - 19 - 2' in source
    for weeks in range(1, 66):
        for per_week in range(1, 25):
            history = min(weeks * per_week, 500 - 2 * weeks - 24 - 19 - 2)
            assert history + 2 * weeks + 24 + 19 + 2 <= 500
    assert 'xloc=xloc.bar_time' in source
    assert 'label.new(bar_index' not in source and 'line.new(bar_index' not in source


def test_native_fixture_calls_the_delivered_engine_with_independent_cases():
    fixture = Path(__file__).with_name('monday_range_v1_11_native_checks.pine').read_text()
    assert 'estimatedProfile(' in fixture and 'keyEngine(' in fixture
    for case in ('partial day', 'midnight', 'gap invalidates', 'removes touched', '23-hour', '25-hour', 'full week'):
        assert case in fixture
    assert 'MONDAY_V111_NATIVE_CHECKS_PASS' in fixture


if __name__ == '__main__':
    import sys
    target = Path(sys.argv[1])
    fixture = Path(__file__).with_name('monday_range_v1_11_native_checks.pine').read_text()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SOURCE.read_text() + '\n' + fixture)
    print(target)
