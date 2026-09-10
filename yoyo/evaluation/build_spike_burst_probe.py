"""Build native Pine engineering assertions from SPIKE Burst's actual helpers.

Only synthetic inputs are used. No price cache, market outcomes, or fitted
parameters are read. Cases are frozen examples of the public engineering
contract: one-factor signal negative controls, inclusive episode boundaries,
outward risk rounding, and adversarial stop/trailing chronology. The generated
script must execute in TradingView; generating it is NOT a Pine PASS receipt.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Dict, Tuple, Union


Number = Union[int, float, None]
BEGIN = "// BEGIN BURST PURE HELPERS\n"
END = "// END BURST PURE HELPERS"
DEFAULT_SOURCE = Path("yoyo/evaluation/pine/spike_burst_v1.pine")
BURST_FIELDS = (
    "side", "o", "h", "l", "c", "md", "previousMd", "sb", "zoneHigh",
    "zoneLow", "ropeHigh", "ropeLow", "rv", "expansion", "volGate",
    "trGate", "bodyGate", "endGate",
)
LONG: Tuple[Number, ...] = (1, 102, 120, 100, 115, 3, 2, 2.5, 110, 90, 108, 94, 4, 3, 4, 3, .55, .75)
SHORT: Tuple[Number, ...] = (-1, 98, 100, 80, 85, -3, -2, -2.5, 110, 90, 106, 92, 4, 3, 4, 3, .55, .75)


@dataclass(frozen=True)
class BurstCase:
    name: str
    base: Tuple[Number, ...]
    changes: Dict[str, Number]
    passes: bool

    @property
    def values(self) -> Tuple[Number, ...]:
        return tuple(self.changes.get(field, value) for field, value in zip(BURST_FIELDS, self.base))


BURST_CASES = (
    BurstCase("long inclusive volume/TR/close-location gates", LONG, {}, True),
    BurstCase("short exact mirrored launch", SHORT, {}, True),
    BurstCase("long exact body threshold", LONG, {"o": 104}, True),
    BurstCase("short exact body threshold", SHORT, {"o": 96}, True),
    BurstCase("volume just below gate", LONG, {"rv": 3.999}, False),
    BurstCase("TR expansion just below gate", LONG, {"expansion": 2.999}, False),
    BurstCase("body just below gate", LONG, {"o": 104.001}, False),
    BurstCase("close location just below gate", LONG, {"c": 114.999}, False),
    BurstCase("close must be strictly beyond frozen box", LONG, {"zoneHigh": 115}, False),
    BurstCase("close must be strictly beyond six-MA rope", LONG, {"ropeHigh": 115}, False),
    BurstCase("main line must accelerate", LONG, {"previousMd": 3}, False),
    BurstCase("main line must exceed signal", LONG, {"sb": 3}, False),
    BurstCase("wrong direction body", LONG, {"o": 116}, False),
    BurstCase("missing volume ratio", LONG, {"rv": None}, False),
    BurstCase("missing expansion", LONG, {"expansion": None}, False),
    BurstCase("missing previous main line", LONG, {"previousMd": None}, False),
    BurstCase("missing box", LONG, {"zoneLow": None}, False),
    BurstCase("missing rope", LONG, {"ropeLow": None}, False),
    BurstCase("zero candle span", LONG, {"h": 100}, False),
    BurstCase("invalid direction", LONG, {"side": 0}, False),
    BurstCase("short volume below gate", SHORT, {"rv": 3.999}, False),
    BurstCase("short direction-side close below gate", SHORT, {"c": 85.001}, False),
    BurstCase("short box equality rejected", SHORT, {"zoneLow": 85}, False),
    BurstCase("short rope equality rejected", SHORT, {"ropeLow": 85}, False),
    BurstCase("short loss of acceleration", SHORT, {"previousMd": -3}, False),
    BurstCase("short signal equality rejected", SHORT, {"sb": -3}, False),
)

# age,count,side,md,frozenBand,close,zoneHigh,zoneLow
WINDOW_CASES = (
    ("release bar is age zero", (0, 6, 1, .11, .10, 105, 110, 90), True),
    ("sixth bar included", (5, 6, 1, .11, .10, 105, 110, 90), True),
    ("seventh bar excluded", (6, 6, 1, .11, .10, 105, 110, 90), False),
    ("pre-release excluded", (-1, 6, 1, .11, .10, 105, 110, 90), False),
    ("single-bar window includes release", (0, 1, 1, .11, .10, 105, 110, 90), True),
    ("single-bar window excludes next bar", (1, 1, 1, .11, .10, 105, 110, 90), False),
    ("frozen band equality cancels", (1, 6, 1, .10, .10, 105, 110, 90), False),
    ("return inside frozen band cancels", (1, 6, 1, .09, .10, 105, 110, 90), False),
    ("opposite-direction main line cancels", (1, 6, 1, -.11, .10, 105, 110, 90), False),
    ("opposite box edge equality allowed", (1, 6, 1, .11, .10, 90, 110, 90), True),
    ("opposite box breach cancels", (1, 6, 1, .11, .10, 89.99, 110, 90), False),
    ("short sixth bar included", (5, 6, -1, -.11, .10, 95, 110, 90), True),
    ("short seventh bar excluded", (6, 6, -1, -.11, .10, 95, 110, 90), False),
    ("short opposite box breach", (1, 6, -1, -.11, .10, 110.01, 110, 90), False),
    ("frozen .10 keeps md .11 qualified", (2, 6, 1, .11, .10, 105, 110, 90), True),
    ("recomputed .13 would incorrectly cancel", (2, 6, 1, .11, .13, 105, 110, 90), False),
)

# side,entry,extreme,ATR,floorATR,bufferATR,tick -> stop,risk,valid
RISK_CASES = (
    ("long ATR floor", (1, 100, 99, 2, 2, .2, .01), (96, 4, True)),
    ("short ATR floor", (-1, 100, 101, 2, 2, .2, .01), (104, 4, True)),
    ("long wider structure", (1, 100, 95, 2, 2, .2, .01), (94.6, 5.4, True)),
    ("short wider structure", (-1, 100, 105, 2, 2, .2, .01), (105.4, 5.4, True)),
    ("long outward rounding", (1, 100, 97.991, 1, 2, 0, .01), (97.99, 2.01, True)),
    ("short outward rounding", (-1, 100, 102.009, 1, 2, 0, .01), (102.01, 2.01, True)),
    ("missing ATR", (1, 100, 95, None, 2, .2, .01), (None, None, False)),
    ("zero ATR", (1, 100, 95, 0, 2, .2, .01), (None, None, False)),
    ("missing structure", (1, 100, None, 2, 2, .2, .01), (None, None, False)),
    ("invalid tick", (1, 100, 95, 2, 2, .2, 0), (None, None, False)),
    ("invalid side", (0, 100, 95, 2, 2, .2, .01), (None, None, False)),
    ("nonpositive outward stop invalid", (1, 1, .5, 2, 2, .2, .01), (None, None, False)),
)

# side,entry,risk,priorProtection,priorPeak,wasArmed,O,H,L,C,ATR,armR,distanceATR,tick
# -> alive,protection,peakR,currentR,exitPrice,armed. Expected values are literal
# hand-worked path examples, not values produced by a Python port of f_path.
PATH_CASES = (
    ("long close arms / old stop operative", (1, 100, 2, 98, 0, False, 100, 108, 99, 104, 1, 2, 4, .01), (True, 100, 4, 2, None, True)),
    ("wick alone cannot arm trailing", (1, 100, 2, 98, 0, False, 100, 108, 99, 103, 1, 2, 4, .01), (True, 98, 4, 1.5, None, False)),
    ("long adverse gap and stop priority", (1, 100, 2, 98, 1.5, False, 95, 140, 94, 130, 1, 2, 4, .01), (False, 98, 1.5, -2.5, 95, False)),
    ("stop equality cannot add peak", (1, 100, 2, 98, 1.5, False, 100, 140, 98, 130, 1, 2, 4, .01), (False, 98, 1.5, -1, 98, False)),
    ("long ATR rise cannot loosen", (1, 100, 2, 100, 4, True, 104, 110, 101, 106, 3, 2, 4, .01), (True, 100, 5, 3, None, True)),
    ("long remains armed after retrace", (1, 100, 2, 100, 4, True, 102, 103, 100.5, 101, 1, 2, 4, .01), (True, 100, 4, .5, None, True)),
    ("long next bar touches ratcheted stop", (1, 100, 2, 100, 4, True, 103, 106, 100, 104, 1, 2, 4, .01), (False, 100, 4, 0, 100, True)),
    ("long next stop gap cannot use better old stop", (1, 100, 2, 100, 4, True, 99, 107, 98, 106, 1, 2, 4, .01), (False, 100, 4, -.5, 99, True)),
    ("short mirror close arms", (-1, 100, 2, 102, 0, False, 100, 101, 92, 96, 1, 2, 4, .01), (True, 100, 4, 2, None, True)),
    ("short adverse gap and stop priority", (-1, 100, 2, 102, 1.5, False, 105, 106, 60, 70, 1, 2, 4, .01), (False, 102, 1.5, -2.5, 105, False)),
    ("short ATR rise cannot loosen", (-1, 100, 2, 100, 4, True, 96, 99, 90, 94, 3, 2, 4, .01), (True, 100, 5, 3, None, True)),
    ("short next bar touches ratcheted stop", (-1, 100, 2, 100, 4, True, 97, 100, 94, 96, 1, 2, 4, .01), (False, 100, 4, 0, 100, True)),
    ("long ratchet rounds outward", (1, 100, 2, 98, 0, False, 103, 106, 102, 104.019, 1, 2, 4, .01), (True, 100.01, 3, 2.0095, None, True)),
    ("short ratchet rounds outward", (-1, 100, 2, 102, 0, False, 97, 98, 94, 95.981, 1, 2, 4, .01), (True, 99.99, 3, 2.0095, None, True)),
    ("missing current ATR holds old protection", (1, 100, 2, 100, 4, True, 104, 110, 101, 106, None, 2, 4, .01), (True, 100, 5, 3, None, True)),
)


def extract_helpers(source_text: str) -> str:
    """Return the exact delimited helper bytes (Unicode text), fail on ambiguity."""
    if source_text.count(BEGIN) != 1 or source_text.count(END) != 1:
        raise ValueError("Expected exactly one BURST PURE HELPERS block")
    before, body = source_text.split(BEGIN, 1)
    if END in before:
        raise ValueError("End marker precedes start marker")
    return body.split(END, 1)[0]


def _literal(value: Union[Number, bool]) -> str:
    if value is None:
        return "na"
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


def _call(function: str, values: tuple) -> str:
    return function + "(" + ", ".join(_literal(x) for x in values) + ")"


def _matches(variable: str, value: Union[Number, bool]) -> str:
    if value is None:
        return f"na({variable})"
    if isinstance(value, bool):
        return variable if value else f"not {variable}"
    return f"f_eq({variable}, {_literal(value)})"


def assertion_body() -> Tuple[str, int]:
    lines = ["if barstate.isfirst"]
    for case in BURST_CASES:
        expression = _call("f_burst", case.values)
        lines.append(f'    f_check({expression if case.passes else "not " + expression}, "{case.name}")')
    for name, args, expected in WINDOW_CASES:
        expression = _call("f_window", args)
        lines.append(f'    f_check({expression if expected else "not " + expression}, "{name}")')
    for prefix, function, cases in (("risk", "f_risk", RISK_CASES), ("path", "f_path", PATH_CASES)):
        for index, (name, args, expected) in enumerate(cases):
            variables = [f"{prefix}{index}_{part}" for part in range(len(expected))]
            lines.append(f'    [{", ".join(variables)}] = {_call(function, args)}')
            conditions = " and ".join(_matches(variable, value) for variable, value in zip(variables, expected))
            lines.append(f'    f_check({conditions}, "{name}")')
    # Unlike independent tuple tests, these calls feed the actual native result
    # into the next bar. They catch accidental tests against newly computed stops.
    lines.extend([
        '    [seqA, seqStop, seqPeak, seqR, seqExit, seqArmed] = f_path(1, 100, 2, 98, 0, false, 100, 108, 99, 104, 1, 2, 4, 0.01)',
        '    [seqB, seqStopB, seqPeakB, seqRB, seqExitB, seqArmedB] = f_path(1, 100, 2, seqStop, seqPeak, seqArmed, 103, 106, 100, 104, 1, 2, 4, 0.01)',
        '    f_check(seqA and not seqB and f_eq(seqStop, 100) and f_eq(seqExitB, 100) and f_eq(seqPeakB, seqPeak), "native chained protection becomes operative next bar")',
        '    [prefixA, prefixStop, prefixPeak, prefixR, prefixExit, prefixArmed] = f_path(1, 100, 2, 98, 0, false, 100, 108, 99, 104, 1, 2, 4, 0.01)',
        '    f_check(prefixA == seqA and f_eq(prefixStop, seqStop) and f_eq(prefixPeak, seqPeak) and f_eq(prefixR, seqR), "replaying identical prefix after future bar stays identical")',
    ])
    count = sum(line.startswith("    f_check(") for line in lines)
    return "\n".join(lines) + "\n", count


def build(source: Path, output: Path) -> int:
    if source.resolve() == output.resolve():
        raise ValueError("Probe output must not overwrite indicator source")
    source_text = source.read_text(encoding="utf-8")
    helpers = extract_helpers(source_text)
    cases, count = assertion_body()
    header = (
        '//@version=6\nindicator("SPIKE Burst V1 · engineering probe", overlay=false)\n'
        f'// Source SHA256: {hashlib.sha256(source_text.encode("utf-8")).hexdigest()}\n'
        '// Synthetic contract verification only; not a return or signal-quality estimate.\n'
    )
    checks = '''
f_check(bool condition, string name) =>
    if not condition
        runtime.error("SPIKE Burst contract failed: " + name)
    true
f_eq(float a, float b) =>
    not na(a) and not na(b) and math.abs(a - b) < 0.000001

'''
    footer = f'''
var table result = table.new(position.middle_center, 1, 1)
if barstate.islast
    table.cell(result, 0, 0, "SPIKE Burst · {count} Pine contracts PASS\\nSynthetic engineering cases · no return estimate", text_color=color.teal)
plot(na, display=display.none)
'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + helpers + checks + cases + footer, encoding="utf-8")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Generated {build(args.source, args.output)} synthetic Pine checks; execute in TradingView to verify.")
