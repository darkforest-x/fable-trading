"""Audit V12.7 against the immutable delivered V12.6 source.

V12.7 adds one isolated input block (held-break age cap) and a reviewable
replacement receipt. Removing the block and reversing the receipt must
recover the entire parent byte for byte. The only behavioral replacements are
the two held-evidence lifetimes; the rest are title, comment and panel text.
Native Pine compilation and chart behavior are separate checks.
"""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).parent
PINE = HERE.parents[1] / 'yoyo/evaluation/pine'


def _source():
    return (PINE / 'spike_burst_v12_7.pine').read_text()


def test_full_parent_restored_by_receipt():
    parent = (PINE / 'spike_burst_v12_6.pine').read_bytes()
    assert hashlib.sha256(parent).hexdigest() == '8a72c18b65e50862ca6dee4e4d58cac4c1e1c7d38ccd3718e7f0228e970ee2db'
    source = _source()
    blocks = re.findall(r'// BEGIN V127 (\w+)\n(.*?)// END V127 \1\n', source, re.S)
    assert [name for name, _ in blocks] == ['INPUTS']
    assert not re.search(r'alertcondition|request\.security|strategy\.|array\.|:=', blocks[0][1])
    restored = re.sub(r'// BEGIN V127 (\w+)\n.*?// END V127 \1\n', '', source, flags=re.S)
    receipt = json.loads((HERE / 'fixtures/spike_v127_held_age_replacements.json').read_text())
    for old, new in reversed(receipt):
        assert restored.count(new) == 1
        restored = restored.replace(new, old)
    assert restored == parent.decode()


def test_cap_touches_only_both_held_lifetimes_and_falls_back_to_line_life():
    source = _source()
    assert 'int v127HeldLife = v127CapHeld ? math.min(v127HeldMax, v10Life) : v10Life' in source
    uses = re.findall(r'^.*\bv127HeldLife\b.*$', source, re.M)
    assert len(uses) == 4  # definition, chart hold, HTF hold, panel text
    assert 'bar_index - held.brokenBar > v127HeldLife' in source
    assert 'bar_index - held.visibleBar > v127HeldLife' in source
    # Structure lifetime and the box-first branch still use the original life.
    assert 'if bar_index - item.born > v10Life or y <= 0' in source
    assert 'bool boxFirst = v112BreakChart or v112BreakHtf' in source


def test_version_identity():
    source = _source()
    assert 'indicator("SPIKE V12.7 · 突破时限", shorttitle="SPIKE V12.7",' in source
    assert "int v127HeldMax = input.int(8," in source
    # Preregistered rule in the experiment plan: later-segment mean net R was
    # not non-inferior, so the cap ships opt-in and OFF by default.
    assert "bool v127CapHeld = input.bool(false," in source
