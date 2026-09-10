"""Build native Pine full-state regression fixtures from the actual Burst block.

The state-machine text and pure helpers are extracted byte-for-byte, then run
inside ten distinct Pine call scopes. Only its input series are synthetic.
No market OHLCV, return scoring, parameter search or Python state mirror is used.
The invalid-ATR fixture deliberately supplies an impossible scale to exercise
risk-reference failure independently of an otherwise valid burst decision.
"""
from pathlib import Path
import argparse
import hashlib
import re
import textwrap


BEGIN_STATE = "// BEGIN BURST STATE MACHINE\n"
END_STATE = "// END BURST STATE MACHINE"
BEGIN_HELPERS = "// BEGIN BURST PURE HELPERS\n"
END_HELPERS = "// END BURST PURE HELPERS"

FIXTURE = r'''
// Each written call owns a separate persistent copy of the ACTUAL state block.
// Fixtures intentionally bypass feature warmup: they test the state consumer,
// while indicator feature warmup is an independent production-source contract.
f_fixture(int scenario) =>
    // One explicit preroll chart bar supplies atr[1] to fixture step zero.
    // The extracted state still uses the real bar_index without modification.
    int b = bar_index - 1
    float u = syminfo.mintick * 100000.0
    bool ready = true
    bool showZone = false
    bool showRisk = false
    bool showMilestones = false
    string direction = "双向"
    float open = 100.0 * u
    float high = 100.4 * u
    float low = 99.6 * u
    float close = 100.0 * u
    float md = 0.0
    float sb = 0.0
    float atr = (b >= 12 ? 1.3 : 1.0) * u
    float rv = 1.0
    float expansion = 1.0
    float pastWidth = 1.0
    float pastCrosses = 3.0
    float ropeHigh = 100.2 * u
    float ropeLow = 99.8 * u
    float recentLow = 99.0 * u
    float recentHigh = 101.0 * u
    if b >= 12 and b <= 18
        open := 100.0 * u
        high := 100.6 * u
        low := 99.9 * u
        close := 100.3 * u
        md := 0.11 * u
        sb := 0.05 * u
    // On bar13, dynamic band=.13 while the pending episode owns band=.10.
    // MD=.11 remains a valid pending launch and MUST NOT be cancelled.
    if b == 13 and scenario == 3
        md := 0.09 * u
    bool strongBar = b == 14 or (b == 18 and scenario == 2)
    if strongBar
        open := 100.0 * u
        high := 104.2 * u
        low := 99.0 * u
        close := 104.0 * u
        md := 0.30 * u
        sb := 0.10 * u
        rv := scenario == 4 ? 3.99 : scenario == 2 and b == 14 ? 1.0 : 5.0
        expansion := 4.0
        if scenario == 5
            atr := 100.0 * u
    if b >= 15 and b <= 17 and (scenario == 0 or scenario == 1)
        open := 104.0 * u
        high := 120.0 * u
        low := 104.0 * u
        close := 115.0 * u
        md := 0.50 * u
        sb := 0.20 * u
        rv := 5.0
        expansion := 4.0
        if b == 16
            open := 115.0 * u
            high := 116.0 * u
            low := 111.0 * u
            close := 114.0 * u
        if b == 17
            open := 105.0 * u
            high := 130.0 * u
            low := 104.0 * u
            close := 120.0 * u
    // Twelve new quiet bars form while the existing path is still active.
    // Bar27 then both hits protection and qualifies as a fresh strong release.
    // The actual state consumer must exit without re-entering on that bar.
    if scenario == 6 and b >= 15 and b <= 26
        open := 104.0 * u
        high := 104.2 * u
        low := 103.9 * u
        close := 104.0 * u
        md := 0.0
        sb := 0.0
    if scenario == 6 and b == 27
        open := 100.0 * u
        high := 112.0 * u
        low := 98.0 * u
        close := 110.0 * u
        md := 0.30 * u
        sb := 0.10 * u
        rv := 5.0
        expansion := 4.0
    if scenario == 1
        float originalHigh = high
        high := 200.0 * u - low
        low := 200.0 * u - originalHigh
        open := 200.0 * u - open
        close := 200.0 * u - close
        md := -md
        sb := -sb
        recentHigh := 200.0 * u - recentLow
    float middle = 100.0 * u + md
    // Price leads while both oscillator lines remain exactly zero. These
    // fixtures test the prior qualified box before this candle can enlarge it.
    if scenario >= 7 and b >= 12 and b <= 13
        open := (b == 12 ? 100.0 : 104.0) * u
        high := (b == 12 ? 104.2 : 108.2) * u
        low := (b == 12 ? 99.0 : 104.0) * u
        close := (b == 12 ? 104.0 : 108.0) * u
        md := 0.0
        sb := 0.0
        rv := 5.0
        expansion := 4.0
        middle := (scenario == 9 ? (b == 12 ? 99.9 : 99.8) : (b == 12 ? 100.2 : 100.4)) * u
        if scenario == 8
            pastWidth := 4.0
'''

RETURNS = "    [burstUp, burstDown, exitUp, exitDown, trendSide, pendingSide, quietCount, risk, protection, peakR, currentR, trailArmed, visibleProtection]\n"

ASSERTIONS = r'''
var array<int> checkCount = array.new_int(1, 0)
f_eq(float a, float b) =>
    not na(a) and not na(b) and math.abs(a-b) <= 1e-7 * math.max(1.0, math.max(math.abs(a), math.abs(b)))
// Decimal expectations may sit one tick away after outward float rounding.
// Keep R comparisons separate: an instrument price tick is not an R tolerance.
f_priceEq(float a, float b) =>
    not na(a) and not na(b) and math.abs(a-b) <= 1.01 * syminfo.mintick + 1e-12 * math.max(math.abs(a), math.abs(b))
f_check(bool passed, string title) =>
    if not passed
        runtime.error("BURST STATE FAIL: " + title)
    array.set(checkCount, 0, array.get(checkCount, 0) + 1)

[up0,dn0,xu0,xd0,side0,pend0,q0,r0,p0,peak0,cur0,arm0,visible0] = f_fixture(0)
[up1,dn1,xu1,xd1,side1,pend1,q1,r1,p1,peak1,cur1,arm1,visible1] = f_fixture(1)
[up2,dn2,xu2,xd2,side2,pend2,q2,r2,p2,peak2,cur2,arm2,visible2] = f_fixture(2)
[up3,dn3,xu3,xd3,side3,pend3,q3,r3,p3,peak3,cur3,arm3,visible3] = f_fixture(3)
[up4,dn4,xu4,xd4,side4,pend4,q4,r4,p4,peak4,cur4,arm4,visible4] = f_fixture(4)
[up5,dn5,xu5,xd5,side5,pend5,q5,r5,p5,peak5,cur5,arm5,visible5] = f_fixture(5)
[up6,dn6,xu6,xd6,side6,pend6,q6,r6,p6,peak6,cur6,arm6,visible6] = f_fixture(6)
[up7,dn7,xu7,xd7,side7,pend7,q7,r7,p7,peak7,cur7,arm7,visible7] = f_fixture(7)
[up8,dn8,xu8,xd8,side8,pend8,q8,r8,p8,peak8,cur8,arm8,visible8] = f_fixture(8)
[up9,dn9,xu9,xd9,side9,pend9,q9,r9,p9,peak9,cur9,arm9,visible9] = f_fixture(9)
float unit = syminfo.mintick * 100000.0
int step = bar_index - 1
if barstate.isconfirmed
    if step == 11
        f_check(q0 == 12 and pend0 == 0 and side0 == 0, "12 closed quiet bars qualify")
        f_check(q1 == 12 and not up1 and not dn1, "quiet qualification is not a burst")
        f_check(q7 == 12 and q8 == 12 and q9 == 12, "price-first fixtures own prior qualified quiet")
    if step == 12
        f_check(pend0 == 1 and pend1 == -1 and not up0 and not dn1, "release opens pending only")
        f_check(up7 and side7 == 1 and pend7 == 0 and q7 == 0, "price-first burst emits before MD release and consumes quiet")
        f_check(f_eq(peak7,0) and f_eq(cur7,0) and na(visible7), "price-first signal candle has no post-entry excursion")
        f_check(not up8 and side8 == 0 and q8 == 13, "price-first burst requires prior MA compression")
        f_check(not up9 and side9 == 0 and q9 == 13, "price-first burst rejects opposing ZLEMA slope")
    if step == 13
        f_check(pend0 == 1 and q0 == 0, "new ATR band cannot cancel frozen long window")
        f_check(pend1 == -1 and q1 == 0, "new ATR band cannot cancel frozen short window")
        f_check(pend3 == 0 and not up3, "return inside frozen band cancels episode")
        f_check(not up7 and side7 == 1 and f_eq(r7,r7[1]), "price-first repeated force cannot reset or duplicate active reference")
        f_check(not up8 and not up9, "price-first negative controls remain silent")
    if step == 14
        f_check(up0 and not dn0 and side0 == 1 and pend0 == 0, "later strong candle emits once")
        f_check(dn1 and not up1 and side1 == -1 and pend1 == 0, "short mirrored later burst")
        f_check(f_eq(peak0,0) and f_eq(cur0,0) and na(visible0), "signal candle has no post-entry excursion")
        f_check(f_eq(peak1,0) and f_eq(cur1,0) and na(visible1), "short signal candle has no excursion")
        f_check(not up2 and pend2 == 1, "insufficient volume remains pending")
        f_check(not up3 and side3 == 0 and pend3 == 0, "cancelled launch cannot revive")
        f_check(not up4 and side4 == 0, "one failed required gate rejects burst")
        f_check(up5 and side5 == 0 and na(r5) and na(cur5), "invalid risk retains arrow but no fabricated R")
        f_check(not up7 and side7 == 1, "later MD release cannot duplicate price-first episode")
    if step == 15
        f_check(not up0 and not dn1, "strong later candles do not duplicate consumed episode")
        f_check(side0 == 1 and arm0 and f_priceEq(p0,109.8*unit), "confirmed 2R arms close-ATR ratchet")
        f_check(f_priceEq(visible0,98.74*unit) and visible0 < 104*unit and p0 > 104*unit, "new protection cannot stop its creation candle")
        f_check(side1 == -1 and arm1 and f_priceEq(p1,90.2*unit) and f_priceEq(visible1,101.26*unit), "short next-bar protection mirror")
    if step == 16
        f_check(f_eq(p0,p0[1]) and f_eq(p1,p1[1]), "ratchet never loosens on pullback")
    if step == 17
        f_check(xu0 and side0 == 0 and not up0, "operative long protection exits")
        f_check(xd1 and side1 == 0 and not dn1, "operative short protection exits")
        f_check(f_eq(cur0,unit/r0) and f_eq(cur1,unit/r1), "adverse gaps use open beyond prior protection")
        f_check(f_eq(peak0,peak0[1]) and f_eq(peak1,peak1[1]), "stop bar cannot increase recorded peak")
    if step == 18
        f_check(not up2 and pend2 == 0 and side2 == 0, "release plus five excludes age six")
        f_check(side0 == 0 and not up0 and f_eq(cur0,cur0[1]), "exited path cannot resurrect or change R")
    if step == 26
        f_check(side6 == 1 and q6 == 12 and not up6, "new quiet structure can form without overwriting active trend")
    if step == 27
        f_check(xu6 and not up6 and side6 == 0 and pend6 == 0, "stop and valid new release cannot exit and re-enter together")
'''


def extract(source_text, begin, end):
    """Require unique exact source boundaries; missing/drifted markers fail."""
    if source_text.count(begin) != 1 or source_text.count(end) != 1:
        raise ValueError("Expected one source block: " + begin.strip())
    return source_text.split(begin, 1)[1].split(end, 1)[0]


def render(source_text):
    """Return probe and expected executed checks; no fixture outcomes evaluated."""
    helpers = extract(source_text, BEGIN_HELPERS, END_HELPERS)
    state = extract(source_text, BEGIN_STATE, END_STATE)
    indicator_match = re.search(r"^indicator\([^\n]*\)\n", source_text, re.M)
    if not indicator_match:
        raise ValueError("Missing one-line production indicator declaration")
    globals_text = source_text[indicator_match.end():source_text.index(BEGIN_HELPERS)]
    count = len(re.findall(r"^\s+f_check\(", ASSERTIONS, re.M))
    digest = hashlib.sha256(state.encode()).hexdigest()
    footer = f'''
var table result = table.new(position.middle_center, 1, 1)
if barstate.islast
    int passed = array.get(checkCount, 0)
    bool complete = passed == {count}
    table.cell(result, 0, 0, complete ? "BURST STATE · " + str.tostring(passed) + "/{count} PASS\\n10 independent synthetic state paths\\nActual source block · no return estimate" : "WAIT / " + str.tostring(passed) + "/{count} checks\\nAt least 29 closed chart bars required", text_color=complete ? color.teal : color.orange)
plot(na, display=display.none)
'''
    header = '//@version=6\nindicator("SPIKE BURST · native state probe", overlay=false, max_labels_count=300)\n'
    lineage = "// Extracted production state SHA256: " + digest + "\n"
    probe = header + lineage + globals_text + helpers + FIXTURE + textwrap.indent(state, "    ") + RETURNS + ASSERTIONS + footer
    return probe, count


def build(source, output):
    """Write only the requested engineering probe; return count, not PASS."""
    text, count = render(Path(source).read_text())
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("yoyo/evaluation/pine/spike_burst_v1.pine"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print("Generated %d native full-state assertions; execute in TradingView to validate" % build(args.source, args.output))
