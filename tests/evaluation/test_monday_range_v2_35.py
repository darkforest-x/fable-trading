"""Guard the chart-specific correction; native fixtures exercise admission timing.

The same fixed5m engine serves two chart routes, so chart scope is part of the
signal contract. No historical performance inference is made by these checks.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_33.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_35.pine').read_text()


def section(s, first, last):
    return s.split(first, 1)[1].split(last, 1)[0]


def test_key_module_preserved():
    assert OLD[OLD.index('type WfTrade'):] == NEW[NEW.index('type WfTrade'):]


def test_protection_and_signals_preserved():
    assert section(OLD, 'txBufferPrice(', 'txPack(') == section(NEW, 'txBufferPrice(', 'txPack(')


def test_original_observation_and_eligibility_preserved():
    assert section(OLD, 'txObserve(', '// Only the entry close') == section(NEW, 'txObserve(', '// An admission records')


def test_five_minute_chart_owns_new_route():
    route = section(NEW, 'txAdmissions(', '// Only the entry close')
    assert 'if entryTf == "5" and confirmationEnabled' in route
    assert 'quarterClose and quarterSide != 0' in route
    assert 'else if txEntryEligible(' in route
    engine = section(NEW, 'txEngine() =>', 'txConfirmed() =>')
    assert 'txAdmissions(confirmation, watch, txEntryTf, txFiveConfirm,' in engine
    assert 'if txEntryTf == "15" and txFiveConfirm' not in NEW


def test_new_version_and_defaults():
    assert 'shorttitle="区间翻身 V2.35"' in NEW
    assert 'txFiveConfirm = input.bool(true' in NEW
    assert 'txFiveWindow = input.int(5' in NEW
    assert 'V235_FIXTURE' not in NEW
