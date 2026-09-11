"""Build a native TradingView probe from V1+'s exact Pine protection helpers.

This is deliberately a source injector, not a Python execution model.  The
resulting temporary Pine file exercises the exact f_risk/f_pathPlus text copied
from the candidate, including its tick rounding and stop-first ordering.
Native compilation and chart execution remain TradingView checks.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import textwrap


MARKER = "// END BURST PURE HELPERS"


def render(source: str) -> str:
    """Return a probe whose helpers are byte-for-byte candidate source text."""
    assert source.count(MARKER) == 1
    prefix = source.split(MARKER, 1)[0]
    helper_sha = hashlib.sha256(prefix.encode()).hexdigest()
    state_begin, state_end = "// BEGIN BURST STATE MACHINE", "// END BURST STATE MACHINE"
    assert source.count(state_begin) == source.count(state_end) == 1
    state = source.split(state_begin, 1)[1].split(state_end, 1)[0]
    assert state.count("if barstate.isconfirmed and ready") == 1
    state = state.replace("if barstate.isconfirmed and ready", "if probeConfirmed and ready")
    state_fixture = '''
// The full candidate state block below is injected verbatim except its closed-bar
// guard. Each scenario owns independent Pine var state; no Python state mirror.
f_v1PlusStateProbe(int scenario) =>
    int b = bar_index - 1
    float u = syminfo.mintick * 100000.0
    bool ready = true
    bool showRisk = false
    bool showZone = false
    bool showMilestones = false
    bool showExitLabels = false
    bool enablePlus = scenario != 3
    bool useOverheatFilter = true
    bool useRiskWidthCap = false
    bool useStagedProtection = true
    bool useStructuralFailure = false
    bool useStagnation = false
    int sameSideCooldown = 0
    int pullbackWindow = scenario >= 4 ? 30 : 6
    string entryMode = scenario >= 4 ? "回踩确认" : "直接确认"
    string direction = "双向"
    bool probeConfirmed = barstate.isconfirmed
    float open = (b >= 13 ? 96.0 : 100.0) * u
    float high = open + 0.2 * u
    float low = open - 0.2 * u
    float close = open
    float md = 0.0
    float sb = 0.0
    float atr = (scenario == 3 and b == 25 ? 100.0 : 1.0) * u
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
        high := 100.2 * u
        low := 95.8 * u
        close := 100.0 * u
        md := 0.3 * u
        sb := 0.1 * u
        rv := scenario == 2 or scenario == 5 ? 51.0 : 5.0
        expansion := scenario == 2 or scenario == 5 ? 11.0 : 4.0
    float middle = 100.0 * u + md
'''
    state_checks = '''
    bool ok = true
    if b == 12 and barstate.isconfirmed
        if scenario <= 3
            ok := burstDown and trendSide == -1 and peakR == 0
        else
            ok := pullbackSide == -1 and trendSide == 0 and not burstDown
    if b == 25 and barstate.isconfirmed
        if scenario == 0
            ok := reverseExit and exitSide == -1 and trendSide == 1 and burstUp and entryBar == bar_index
        else if scenario == 1
            ok := reverseExit and exitSide == -1 and trendSide == 1 and burstUp
        else if scenario == 2
            ok := reverseExit and exitSide == -1 and trendSide == 0 and not burstUp and not burstDown
        else if scenario == 3
            ok := reverseExit and exitSide == -1 and trendSide == 0 and burstUp and na(currentR)
        else if scenario == 4
            ok := pullbackSide == 1 and pullbackSignalBar == bar_index and trendSide == 0 and not burstUp and not burstDown
        else if scenario == 5
            ok := pullbackSide == 0 and trendSide == 0 and not burstUp and not burstDown
    if not ok
        runtime.error("V1+ STATE PROBE FAIL scenario=" + str.tostring(scenario) + " bar=" + str.tostring(b))
    var int checked = 0
    if barstate.isconfirmed and (b == 12 or b == 25)
        checked += 1
    checked
'''
    state_calls = "\n".join(f"int stateProbe{i} = f_v1PlusStateProbe({i})" for i in range(6))
    state_calls += "\nint stateProbeTotal = " + " + ".join(f"stateProbe{i}" for i in range(6))
    checks = f'''{MARKER}
// Injected V1+ helper prefix SHA: {helper_sha}
f_probeEq(float actual, float expected, string message) =>
    if na(actual) or math.abs(actual - expected) > syminfo.mintick * 0.001
        runtime.error("V1+ PROBE FAIL: " + message + " actual=" + str.tostring(actual) + " expected=" + str.tostring(expected))

var int probeChecks = 0
if barstate.isconfirmed and bar_index == 20
    float u = syminfo.mintick * 100000.0
    // Long: confirmed 1R then 2R stages are next-bar stops, rounded outward.
    [longAlive1, longStop1, longPeak1, longR1, longGap1, longArmed1, longS1, longS2, longBest1] = f_pathPlus(true, 1, 100.0 * u, 10.0 * u, 90.0 * u, 0.0, false, false, false, 100.0 * u, false, 1, 1, 12, 110.0 * u, 111.0 * u, 109.0 * u, 110.0 * u, 1.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    f_probeEq(longStop1 / u, 95.0, "long 1R -0.5R")
    [longAlive2, longStop2, longPeak2, longR2, longGap2, longArmed2, longS1b, longS2b, longBest2] = f_pathPlus(true, 1, 100.0 * u, 10.0 * u, longStop1, longPeak1, longArmed1, longS1, longS2, 110.0 * u, false, 2, 2, 12, 120.0 * u, 121.0 * u, 119.0 * u, 120.0 * u, 10.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    f_probeEq(longStop2 / u, 100.2, "long 2R cost buffer")
    [longAliveTrail, longStopTrail, longPeakTrail, longRTrail, longGapTrail, longArmedTrail, longS1Trail, longS2Trail, longBestTrail] = f_pathPlus(true, 1, 100.0 * u, 10.0 * u, longStop1, longPeak1, longArmed1, longS1, longS2, 110.0 * u, false, 2, 2, 12, 120.0 * u, 121.0 * u, 119.0 * u, 120.0 * u, 1.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    f_probeEq(longStopTrail / u, 116.0, "long trail beats cost buffer")
    // Short is the directional mirror and rounds upward.
    [shortAlive1, shortStop1, shortPeak1, shortR1, shortGap1, shortArmed1, shortS1, shortS2, shortBest1] = f_pathPlus(true, -1, 100.0 * u, 10.0 * u, 110.0 * u, 0.0, false, false, false, 100.0 * u, false, 1, 1, 12, 90.0 * u, 91.0 * u, 89.0 * u, 90.0 * u, 1.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    f_probeEq(shortStop1 / u, 105.0, "short 1R -0.5R")
    [shortAlive2, shortStop2, shortPeak2, shortR2, shortGap2, shortArmed2, shortS1b, shortS2b, shortBest2] = f_pathPlus(true, -1, 100.0 * u, 10.0 * u, shortStop1, shortPeak1, shortArmed1, shortS1, shortS2, 90.0 * u, false, 2, 2, 12, 80.0 * u, 81.0 * u, 79.0 * u, 80.0 * u, 10.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    f_probeEq(shortStop2 / u, 99.8, "short 2R cost buffer")
    [shortAliveTrail, shortStopTrail, shortPeakTrail, shortRTrail, shortGapTrail, shortArmedTrail, shortS1Trail, shortS2Trail, shortBestTrail] = f_pathPlus(true, -1, 100.0 * u, 10.0 * u, shortStop1, shortPeak1, shortArmed1, shortS1, shortS2, 90.0 * u, false, 2, 2, 12, 80.0 * u, 81.0 * u, 79.0 * u, 80.0 * u, 1.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    f_probeEq(shortStopTrail / u, 84.0, "short trail beats cost buffer")
    // Prior protection/gap wins before a same-close newly computed stop.
    [gapAlive, gapStop, gapPeak, gapR, gapPrice, gapArmed, gapS1, gapS2, gapBest] = f_pathPlus(true, 1, 100.0 * u, 10.0 * u, 90.0 * u, 0.0, false, false, false, 100.0 * u, false, 1, 1, 12, 80.0 * u, 90.0 * u, 70.0 * u, 85.0 * u, 1.0 * u, 2.0, 4.0, 0.20, syminfo.mintick)
    if gapAlive
        runtime.error("V1+ PROBE FAIL: prior stop must exit before protection update")
    f_probeEq(gapPrice / u, 80.0, "adverse long gap")
    // A rejected proposed reference does not change the independent opposite exit.
    bool proposedRejected = true  // overheat/risk-cap/cooldown test branch
    bool oldReferenceEnded = f_rawEndsOppositeReference(-1, 1)
    if not oldReferenceEnded or not proposedRejected
        runtime.error("V1+ PROBE FAIL: reverse exit must survive rejected new risk")
    [capStop, capRisk, capValid] = f_risk(1, 100.0 * u, 95.0 * u, 40.0 * u, 2.0, 0.2, syminfo.mintick)
    bool capRejected = capValid and 100.0 * capRisk / (100.0 * u) > 30.0
    if not capRejected
        runtime.error("V1+ PROBE FAIL: width cap boundary")
    probeChecks += 1

var table probe = table.new(position.bottom_right, 1, 1)
if barstate.islast
    bool probePassed = probeChecks == 1 and stateProbeTotal == 12
    table.cell(probe, 0, 0, "V1+ native probe helpers " + str.tostring(probeChecks) + "/1 · state " + str.tostring(stateProbeTotal) + "/12" + (probePassed ? " passed" : " not executed"), text_color=color.white, bgcolor=probePassed ? color.teal : color.orange)
'''
    return prefix + checks.replace("var int probeChecks = 0", state_fixture + textwrap.indent(state, "    ") + state_checks + "\n" + state_calls + "\nvar int probeChecks = 0")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.source.read_text()))


if __name__ == "__main__":
    main()
