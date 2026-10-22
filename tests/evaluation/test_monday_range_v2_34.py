"""Preserve unrelated signal, management and drawing modules during pairing change.

Behavioral boundaries are executed in TradingView by the adjacent native fixture;
these source-level guards prevent accidental edits outside the admission module.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_33.pine').read_text()
NEW = (ROOT / 'yoyo/evaluation/pine/monday_range_v2_34.pine').read_text()

def section(s, first, last):
    return s.split(first, 1)[1].split(last, 1)[0]

def test_key_signal_module_is_identical():
    assert OLD[OLD.index('type WfTrade'):] == NEW[NEW.index('type WfTrade'):]

def test_protection_and_signal_calculation_are_identical():
    assert section(OLD, 'txBufferPrice(', 'txPack(') == section(NEW, 'txBufferPrice(', 'txPack(')

def test_existing_observation_and_base_eligibility_unchanged():
    assert section(OLD, 'txObserve(', '// Only the entry close') == section(NEW, 'txObserve(', '// An admission records')

def test_new_version_and_enabled_default():
    assert 'shorttitle="区间翻身 V2.34"' in NEW
    assert 'txFiveConfirm = input.bool(true' in NEW
    assert 'txFiveWindow = input.int(5' in NEW
    assert 'V234_FIXTURE' not in NEW
