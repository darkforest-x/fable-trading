"""Build an on-platform Pine contract probe from the actual V2.5 pure helpers.

This is an engineering test, not a market simulation or profitability estimate.
Synthetic OHLC tuples exercise stop priority, frozen R, mirrors, wick reclamation
and monotonic protection references. No candle cache or future data is read.
Run the generated unsaved script in TradingView to execute Pine itself.
"""

from __future__ import annotations

import argparse
from pathlib import Path


CASES = r'''
f_check(bool condition, string name) =>
    if not condition
        runtime.error("V2.5 contract failed: " + name)
    true

f_eq(float a, float b) =>
    not na(a) and math.abs(a - b) < 0.000001

if barstate.isfirst
    [l1, c1, p1, g1, s1, a1, b1, d1] = f_rStep(1, 100, 98, 2, true, 0, 0, 100, 107, 99, 104)
    f_check(l1 and f_eq(c1, 2) and f_eq(p1, 3.5) and f_eq(g1, 1.5) and not s1 and a1 and not b1 and not d1, "long / 3R reached")
    [l2, c2, p2, g2, s2, a2, b2, d2] = f_rStep(1, 100, 98, 2, l1, c1, p1, 104, 122, 97, 120)
    f_check(not l2 and s2 and f_eq(c2, -1) and f_eq(p2, 3.5) and not a2 and not b2 and not d2, "same bar stop precedes 10R")
    [l3, c3, p3, g3, s3, a3, b3, d3] = f_rStep(1, 100, 98, 2, l2, c2, p2, 120, 140, 119, 138)
    f_check(not l3 and f_eq(c3, c2) and f_eq(p3, p2) and not s3 and not a3 and not b3 and not d3, "stopped episode never resurrects")
    [l4, c4, p4, g4, s4, a4, b4, d4] = f_rStep(1, 100, 98, 2, true, 1, 2, 95, 125, 94, 120)
    f_check(not l4 and s4 and f_eq(c4, -2.5) and f_eq(p4, 2) and not a4 and not b4 and not d4, "long gap uses worse open")
    [l5, c5, p5, g5, s5, a5, b5, d5] = f_rStep(-1, 100, 102, 2, true, 0, 0, 100, 101, 93, 96)
    f_check(l5 and f_eq(c5, 2) and f_eq(p5, 3.5) and f_eq(g5, 1.5) and a5 and not s5, "short mirror / 3R")
    [l6, c6, p6, g6, s6, a6, b6, d6] = f_rStep(-1, 100, 102, 2, true, 1, 2, 105, 106, 75, 80)
    f_check(not l6 and s6 and f_eq(c6, -2.5) and f_eq(p6, 2) and not a6 and not b6 and not d6, "short gap / stop first")
    [l7, c7, p7, g7, s7, a7, b7, d7] = f_rStep(1, 100, 98, 2, true, 4, 5, 110, 122, 109, 120)
    f_check(l7 and f_eq(c7, 10) and f_eq(p7, 11) and not a7 and not b7 and d7, "milestone only once")
    [l8, c8, p8, g8, s8, a8, b8, d8] = f_rStep(1, 100, 98, 2, true, 0, 0, 100, 101, 98, 100)
    f_check(not l8 and s8 and f_eq(c8, -1), "exact stop equality")
    f_check(f_wickReclaim(1, 101, 104, 99, 102, 103, 100, 100), "long wick reclaim")
    f_check(not f_wickReclaim(1, 101, 104, 99, 102, 99, 100, 100), "previous close must be outside")
    f_check(not f_wickReclaim(1, 99, 104, 98, 102, 103, 100, 100), "body cannot straddle boundary")
    f_check(not f_wickReclaim(1, 101, 104, 100.5, 102, 103, 100, 100), "real wick must touch")
    f_check(f_wickReclaim(-1, 99, 101, 96, 98, 97, 100, 100), "short wick mirror")
    f_check(not f_wickReclaim(-1, 99, 101, 96, 98, 101, 100, 100), "short previous close")
    f_check(not f_wickReclaim(1, 101, 104, 99, 102, 103, na, 100), "missing level cannot trigger")
    f_check(f_eq(f_trailStep(1, na, 100, 101), 100), "long protection activation")
    f_check(na(f_trailStep(1, na, 100, 99)), "long wrong side remains inactive")
    f_check(f_eq(f_trailStep(1, 100, 99, 102), 100), "long cannot loosen")
    f_check(f_eq(f_trailStep(1, 100, 102, 103), 102), "long ratchet")
    f_check(f_eq(f_trailStep(-1, na, 100, 99), 100), "short protection activation")
    f_check(na(f_trailStep(-1, na, 100, 101)), "short wrong side remains inactive")
    f_check(f_eq(f_trailStep(-1, 100, 101, 98), 100), "short cannot loosen")
    f_check(f_eq(f_trailStep(-1, 100, 98, 97), 98), "short ratchet")
    f_check(f_eq(f_trailStep(1, 100, 102, 101), 100), "current MA cannot invent past protection")

var table result = table.new(position.middle_center, 1, 1)
if barstate.islast
    table.cell(result, 0, 0, "V2.5 · 24 Pine contracts PASS\nSynthetic engineering cases · no return estimate", text_color=color.teal)
plot(na, display=display.none)
'''


def build(source: Path, output: Path) -> None:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if "BEGIN V2.5" in line and "PURE HELPERS" in line)
    end = next(i for i in range(start + 1, len(lines)) if "END V2.5" in lines[i] and "PURE HELPERS" in lines[i])
    helpers = "\n".join(lines[start + 1 : end])
    header = '//@version=6\nindicator("IMACD V2.5 · engineering probe", overlay=false)\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + helpers + "\n" + CASES, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("yoyo/evaluation/pine/imacd_dense_mtf_v2_5.pine"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
