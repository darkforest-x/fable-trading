"""New Pine admission must not rewrite raw exits or non-15m logic.

The existing behavioral suite verifies HTF availability and serial execution;
these contracts bind both independently versioned Pine consumers to that gate.
Native compilation remains a separate required check.
"""
from pathlib import Path
import re

import pytest

PINE = Path(__file__).resolve().parents[2] / "yoyo/evaluation/pine"
PAIRS = [("v9", "v9_1", "V9", "V9.1", 7, "标的量能时段过滤"),
         ("v12_2", "v12_3", "V12.2", "V12.3", 16, "历史淡线")]


@pytest.mark.parametrize("old,new,oldname,newname,rows,title", PAIRS)
def test_only_admission_and_diagnostics_change(old, new, oldname, newname, rows, title):
    original = (PINE / f"spike_burst_{old}.pine").read_text()
    current = (PINE / f"spike_burst_{new}.pine").read_text()
    stripped = re.sub(r"(?m)^ *// BEGIN CONFIRMED H1 SMA60 (\w+)\n.*?^ *// END CONFIRMED H1 SMA60 \1\n", "", current, flags=re.S)
    stripped = stripped.replace("// New private version: owner-selected H1 SMA60 admission on 15m only.\n", "")
    stripped = stripped.replace(f"SPIKE {newname}", f"SPIKE {oldname}").replace(f'"{newname} 事件码', f'"{oldname} 事件码')
    stripped = stripped.replace(f'indicator("SPIKE {oldname} · 15m · 1h SMA60"', f'indicator("SPIKE {oldname} · {title}"')
    stripped = stripped.replace(" and h1SmaEntryAllowed", "")
    stripped = stripped.replace(f"table.new(position.top_right, 2, {rows + 1},", f"table.new(position.top_right, 2, {rows},")
    meaningful = lambda text: [line for line in text.splitlines() if line.strip()]
    assert meaningful(stripped) == meaningful(original)


@pytest.mark.parametrize("version", ["v9_1", "v12_3"])
def test_same_confirmed_gate_controls_admission_only(version):
    s = (PINE / f"spike_burst_{version}.pine").read_text()
    assert 'bool useH1Sma60 = input.bool(true,' in s
    assert "useH1Sma60 and timeframe.isminutes and timeframe.multiplier == 15" in s
    assert "float average = ta.sma(close, 60)" in s
    assert "float knownAverage = contiguous >= 60 ? average : na" in s
    assert "[knownAverage[1], time_close[1]]" in s
    assert 'request.security(syminfo.tickerid, "60", f_h1Sma60(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_on)' in s
    assert 'h1SmaCloseTime == time("60")' in s
    assert "rawSignalSide == 1 ? close > h1Sma60 : rawSignalSide == -1 ? close < h1Sma60 : false" in s
    assert "not h1SmaApplies or (h1SmaReady and h1SmaDirectionAllowed)" in s
    assert "bool v9EntryAllowed = bbEntryAllowed and v8EntryAllowed and v9BundleAllowed and h1SmaEntryAllowed" in s
    assert s.index("int rawSignalSide =") < s.index("bool h1SmaEntryAllowed") < s.index("confirmedSignal := confirmedSignal and v9EntryAllowed")
    assert "if rawSignalSide != 0 and (not endedThisBar or oppositeStopSignal) and rawSignalSide != trendSide" in s
    assert "if signalSide != 0 and validRisk" in s


def test_both_versions_use_identical_htf_computation():
    sources = [(PINE / f"spike_burst_{v}.pine").read_text() for v in ["v9_1", "v12_3"]]
    for section in ["INPUT", "HELPER", "GATE", "REASON", "DATA"]:
        pattern = rf"// BEGIN CONFIRMED H1 SMA60 {section}.*?// END CONFIRMED H1 SMA60 {section}"
        assert re.search(pattern, sources[0], re.S).group() == re.search(pattern, sources[1], re.S).group()
