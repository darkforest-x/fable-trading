"""Inject synthetic checks around V6's actual Pine reference block.

No market data or return scoring. The source block is copied verbatim except
the closed-bar guard, which is explicitly driven by a fixture boolean to test
an unconfirmed event. Each written function call owns independent Pine state.
The resulting script is a temporary native TradingView QA artifact, not a
replacement indicator or evidence of strategy profitability.
"""
from pathlib import Path
import argparse
import hashlib
import textwrap


def render(source: str) -> str:
    begin, end = "// BEGIN REFERENCE STATE", "// END REFERENCE STATE"
    assert source.count(begin) == source.count(end) == 1
    state = source.split(begin, 1)[1].split("\n", 1)[1].split(end, 1)[0]
    assert state.count("if barstate.isconfirmed") == 1
    state = state.replace("if barstate.isconfirmed", "if probeConfirmed")
    fixture = '''
f_reverseProbe(int scenario) =>
    int b = bar_index
    float u = syminfo.mintick * 100000.0
    int originalSide = scenario == 1 or scenario == 3 or scenario == 6 ? 1 : -1
    bool ready = true
    bool dataGap = false
    bool showExitLabels = false
    bool probeConfirmed = barstate.isconfirmed and not (scenario == 4 and b == 1)
    float open = 100.0 * u
    float high = (b == 1 and (scenario == 2 or scenario == 7) ? 103.0 : 100.5) * u
    float low = 99.5 * u
    float close = 100.0 * u
    float atr = (scenario == 5 and b == 1 ? 100.0 : 1.0) * u
    float recentLow = 99.0 * u
    float recentHigh = 101.0 * u
    int signalSide = b == 0 ? originalSide : b == 1 ? (scenario == 3 or scenario == 7 ? originalSide : scenario == 6 ? 0 : -originalSide) : 0
    if scenario == 6 and b == 0
        high := 120.0 * u
        low := 80.0 * u
'''
    checks = '''
    bool ok = true
    if b == 0 and barstate.isconfirmed
        ok := referenceStarted and trendSide == originalSide and peakR == 0 and not exitReference
    if b == 1 and barstate.isconfirmed
        if scenario == 0 or scenario == 1
            ok := referenceReverse and exitReference and exitSide == originalSide and exitReferencePrice == close and trendSide == -originalSide and referenceStarted and entryBar == b and peakR == 0 and currentR == 0 and exitPeakR > 0
        else if scenario == 2
            ok := exitReference and not referenceReverse and exitReason == "保护触及" and math.abs(exitReferencePrice / u - 102.0) < 0.0001 and trendSide == 1 and referenceStarted and peakR == 0 and exitCurrentR == -1
        else if scenario == 3
            ok := trendSide == originalSide and entryBar == 0 and not referenceStarted and not exitReference
        else if scenario == 4
            ok := trendSide == originalSide and entryBar == 0 and not referenceStarted and not exitReference and peakR == 0
        else if scenario == 5
            ok := referenceReverse and exitReference and trendSide == 0 and not referenceStarted
        else if scenario == 6
            ok := trendSide == originalSide and entryBar == 0 and not exitReference
        else if scenario == 7
            ok := exitReference and not referenceReverse and trendSide == 0 and not referenceStarted
    if not ok
        runtime.error("REVERSE PROBE FAIL scenario=" + str.tostring(scenario) + " bar=" + str.tostring(b))
    var int checked = 0
    if barstate.isconfirmed and b <= 1
        checked += 1
    checked
'''
    calls = "\n".join(f"int reverseProbe{i} = f_reverseProbe({i})" for i in range(8))
    calls += "\nint reverseProbeTotal = " + " + ".join(f"reverseProbe{i}" for i in range(8))
    calls += '''
var table reverseAudit = table.new(position.bottom_right, 1, 1)
if barstate.islast
    table.cell(reverseAudit, 0, 0, "REVERSE QA " + str.tostring(reverseProbeTotal) + "/16", text_color=color.white, bgcolor=color.teal)
'''
    return source + "\n// Native reference probe SHA " + hashlib.sha256(state.encode()).hexdigest() + "\n" + fixture + textwrap.indent(state, "    ") + checks + "\n" + calls


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.source.read_text()))
