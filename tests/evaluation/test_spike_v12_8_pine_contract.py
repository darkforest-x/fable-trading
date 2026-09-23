"""Protect the V12.6 trading semantics when adding V12.8 chart hints.

The immutable-parent round trip covers every original signal, stop, reverse
exit and display expression. Isolation checks prevent the new hint state from
writing the parent state. These are source contracts, not a Pine interpreter;
native compilation and chart checks are recorded separately in the report.
"""
import hashlib
import json
from pathlib import Path
import re


HERE = Path(__file__).parent
PINE = HERE.parents[1] / "yoyo/evaluation/pine"


def _source():
    return (PINE / "spike_burst_v12_8.pine").read_text()


def _blocks():
    return re.findall(r"// BEGIN V128 (\w+)\n(.*?)// END V128 \1\n", _source(), re.S)


def test_every_parent_expression_is_preserved_byte_for_byte():
    parent = (PINE / "spike_burst_v12_6.pine").read_bytes()
    assert hashlib.sha256(parent).hexdigest() == "8a72c18b65e50862ca6dee4e4d58cac4c1e1c7d38ccd3718e7f0228e970ee2db"
    assert _blocks()
    restored = re.sub(r"// BEGIN V128 (\w+)\n.*?// END V128 \1\n", "", _source(), flags=re.S)
    receipt = json.loads((HERE / "fixtures/spike_v128_roll_hint_replacements.json").read_text())
    for old, new in reversed(receipt):
        assert new in restored
        # Version identity is the only permitted parent edit.
        assert old.replace("V12.6", "V12.8").replace("自适应视觉", "两次加仓提示") == new
        restored = restored.replace(new, old)
    assert restored.encode() == parent


def test_new_blocks_cannot_write_parent_trade_state_or_place_orders():
    for _, block in _blocks():
        code = "\n".join(line.split("//")[0] for line in block.splitlines())
        targets = re.findall(r"^\s*([\w.]+)\s*(?::=|\+=|-=)", code, re.M)
        assert all(name.startswith("v128") for name in targets), targets
        assert not re.search(r"\bstrategy\.|\balert\(", code)
        for target in re.findall(r"\barray\.(?:push|pop|shift|unshift|clear|set|remove)\(\s*(\w+)", code):
            assert target.startswith("v128")
        for target in re.findall(r"\btable\.(?:cell|clear|delete)\(\s*(\w+)", code):
            assert target.startswith("v128")


def test_v128_is_based_on_v126_not_the_separate_v127_candidate():
    source = _source()
    assert 'indicator("SPIKE V12.8 · 两次加仓提示", shorttitle="SPIKE V12.8",' in source
    assert "v127CapHeld" not in source
    assert "v127HeldLife" not in source


def test_hour_adapter_is_confirmed_deduplicated_and_after_frame_entry():
    blocks = dict(_blocks())
    state = blocks["ROLL_HINTS"]
    assert '[open[1], high[1], low[1], close[1], time[1], time_close[1]]' in state
    assert 'gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_on' in state
    assert 'v128H1CloseTime <= time_close : v128H1CloseTime <= time' in state
    assert 'v128H1CloseTime - v128H1Time == V128_H1_MS' in state
    assert 'v128H1CloseTime != v128LastHourClose' in state
    assert 'v128H1Time < v128ExpectedFirstHour' in state
    assert 'if barstate.isconfirmed\n    if not v128Enabled' in state
    # All frames are processed after the original optional early exit logic.
    assert _source().index('// BEGIN V128 ROLL_HINTS') > _source().index('if barstate.isconfirmed and v10Enabled and v112EarlyExit')


def test_hint_count_is_capped_without_capping_structure_updates():
    state = dict(_blocks())["ROLL_HINTS"]
    assert 'v128CandidateEligible and v128CandidateCount < 2' in state
    assert state.count('v128CandidateCount += 1') == 1
    assert state.index('v128StructuralStop := v128Proposal') < state.index('v128CandidateCount < 2')
    assert 'close >= entry + 2.0 * risk and v128H1Close >= entry + 2.0 * risk' in state
    assert 'bar_index > v128StructuralSetBar and low <= v128StructuralStop' in state
    assert 'not v128EpisodeInvalid and not v128EpisodePaused' in state
    assert 'not v128EpisodePaused and not v128EpisodeInvalid' in dict(_blocks())["DATA_WINDOW"]
