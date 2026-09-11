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


def render_v1(source: str) -> str:
    """Exercise the actual V1 qualification and reverse state, not a mirror."""
    begin, end = "// BEGIN BURST STATE MACHINE", "// END BURST STATE MACHINE"
    assert source.count(begin) == source.count(end) == 1
    state = source.split(begin, 1)[1].split(end, 1)[0]
    assert state.count("if barstate.isconfirmed and ready") == 1
    state = state.replace("if barstate.isconfirmed and ready", "if probeConfirmed and ready")
    fixture = '''
f_reverseProbe(int scenario) =>
    int b = bar_index - 1
    float u = syminfo.mintick * 100000.0
    bool ready = true
    bool showRisk = false
    bool showZone = false
    bool showMilestones = false
    bool showExitLabels = false
    string direction = "双向"
    bool probeConfirmed = barstate.isconfirmed and not (scenario == 4 and b == 25)
    float open = (b >= 13 ? 96.0 : 100.0) * u
    float high = open + 0.2 * u
    float low = open - 0.2 * u
    float close = open
    float md = 0.0
    float sb = 0.0
    float atr = (scenario == 5 and b == 25 ? 100.0 : 1.0) * u
    float rv = 1.0
    float expansion = 1.0
    float pastWidth = 1.0
    float pastCrosses = 3.0
    float ropeHigh = open + 0.2 * u
    float ropeLow = open - 0.2 * u
    float recentLow = 95.0 * u
    float recentHigh = 101.0 * u
    if b == 12
        high := 100.2 * u
        low := 95.8 * u
        close := 96.0 * u
        md := -0.3 * u
        sb := -0.1 * u
        rv := 5.0
        expansion := 4.0
    if b == 25
        high := (scenario == 2 ? 103.0 : 100.2) * u
        low := 95.8 * u
        close := (scenario == 2 ? 102.5 : 100.0) * u
        md := 0.3 * u
        sb := 0.1 * u
        rv := 5.0
        expansion := 4.0
        if scenario == 3
            high := 96.2 * u
            low := 91.8 * u
            close := 92.0 * u
            md := -0.3 * u
            sb := -0.1 * u
    if scenario == 1
        open := 200.0 * u - open
        close := 200.0 * u - close
        float mirroredHigh = 200.0 * u - low
        low := 200.0 * u - high
        high := mirroredHigh
        float mirroredRope = 200.0 * u - ropeLow
        ropeLow := 200.0 * u - ropeHigh
        ropeHigh := mirroredRope
        recentLow := 99.0 * u
        recentHigh := 105.0 * u
        md := -md
        sb := -sb
    float middle = 100.0 * u + md
'''
    checks = '''
    bool ok = true
    if b == 12 and barstate.isconfirmed
        ok := (scenario == 1 ? burstUp and trendSide == 1 : burstDown and trendSide == -1) and peakR == 0
    if b == 25 and barstate.isconfirmed
        if scenario == 0 or scenario == 1
            ok := reverseExit and exitSide == (scenario == 1 ? 1 : -1) and trendSide == -exitSide and entryBar == bar_index and peakR == 0 and exitPeakR > 0 and exitReferencePrice == close
        else if scenario == 2
            ok := exitDown and not reverseExit and burstUp and trendSide == 1 and peakR == 0 and exitCurrentR == -1
        else if scenario == 3 or scenario == 4
            ok := not reverseExit and trendSide == -1 and entryBar == 13 and not burstUp and not burstDown
        else if scenario == 5
            ok := reverseExit and trendSide == 0 and burstUp
    if not ok
        runtime.error("V1 REVERSE PROBE FAIL scenario=" + str.tostring(scenario) + " bar=" + str.tostring(b))
    var int checked = 0
    if barstate.isconfirmed and (b == 12 or b == 25)
        checked += 1
    checked
'''
    calls = "\n".join(f"int reverseProbe{i} = f_reverseProbe({i})" for i in range(6))
    calls += "\nint reverseProbeTotal = " + " + ".join(f"reverseProbe{i}" for i in range(6))
    calls += '''
var table reverseAudit = table.new(position.bottom_right, 1, 1)
if barstate.islast
    table.cell(reverseAudit, 0, 0, "V1 REVERSE QA " + str.tostring(reverseProbeTotal) + "/12", text_color=color.white, bgcolor=color.teal)
'''
    marker = "// END BURST PURE HELPERS"
    prefix, suffix = source.split(marker, 1)
    return prefix + marker + "\n" + fixture + textwrap.indent(state, "    ") + checks + suffix + "\n" + calls


def render(source: str) -> str:
    if "// BEGIN BURST STATE MACHINE" in source:
        return render_v1(source)
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
    marker = "// END UNCHANGED V2 RISK HELPERS"
    prefix, suffix = source.split(marker, 1)
    return prefix + marker + "\n// Native reference probe SHA " + hashlib.sha256(state.encode()).hexdigest() + "\n" + fixture + textwrap.indent(state, "    ") + checks + suffix + "\n" + calls


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.source.read_text()))
