"""Audit presentation-only V12.6 against the immutable delivered V12.5 source.

The explicit replacement receipt is reviewable and must not contain admission,
stop, or pairing changes. Removing the two isolated visual blocks and reversing
that receipt must recover the entire parent, not only selected signal snippets.
Native Pine compilation and chart rendering are separate checks.
"""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).parent
PINE = HERE.parents[1] / 'yoyo/evaluation/pine'


def test_full_parent_restored_by_visual_receipt():
    parent = (PINE / 'spike_burst_v12_5.pine').read_bytes()
    assert hashlib.sha256(parent).hexdigest() == 'be7adb778ef552cb61606d9290c0a5ba6957b56b5f5f2f56e4c5ac943c1dde0e'
    source = (PINE / 'spike_burst_v12_6.pine').read_text()
    blocks = re.findall(r'// BEGIN V126 (\w+)\n(.*?)// END V126 \1\n', source, re.S)
    assert {name for name, _ in blocks} == {'INPUTS', 'COLOR_STATE'}
    # New state can only mutate its own v126-prefixed presentation variables.
    for _, block in blocks:
        assigned = re.findall(r'^\s*([\w.]+)\s*:=', block, re.M)
        assert all(name.startswith('v126') for name in assigned)
        assert not re.search(r'alertcondition|request\.security|strategy\.|array\.(push|remove|set)', block)
    restored = re.sub(r'// BEGIN V126 (\w+)\n.*?// END V126 \1\n', '', source, flags=re.S)
    receipt = json.loads((HERE / 'fixtures/spike_v126_visual_replacements.json').read_text())
    for old, new in reversed(receipt):
        assert new in restored
        restored = restored.replace(new, old)
    assert restored == parent.decode()


def test_coloring_consumes_confirmed_frozen_evidence_without_backfill():
    source = (PINE / 'spike_burst_v12_6.pine').read_text()
    block = source.split('// BEGIN V126 COLOR_STATE\n')[1].split('// END V126 COLOR_STATE')[0]
    assert 'if barstate.isconfirmed\n    if dataGap or not pricesValid' in block
    assert 'v126LocalLine := v11LastBroken.copy()' in block
    assert 'v126LocalLine.at(bar_index)' in block
    assert 'if y <= 0 or close <= y' in block
    assert 'bar_index - v126LocalStart < v126BreakBars' in block
    assert 'bar_index - v126HtfStart < v126BreakBars' in block
    assert 'bar_index - v126LocalStart <= 8' in block
    assert 'bar_index - v126HtfStart <= 8' in block
    assert 'bool v126PinReady = barstate.isconfirmed and pricesValid' in block
    assert 'offset=' not in source.split('// BEGIN V126 COLOR_STATE')[1].split('// ───────────── 数据窗口')[0]
