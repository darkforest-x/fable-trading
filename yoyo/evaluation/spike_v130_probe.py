"""Build Pine-native probes from the exact V13.0 state source, not a port.

Artificial OHLC fixtures test implementation only. They do not train a model,
measure market profitability, or confer trading eligibility. The state-machine
oracle is the frozen V12.8 Python confirmation implementation.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from yoyo.evaluation.spike_v128_retest_state import confirmation
from yoyo.evaluation.spike_v130_delivery import CORE, LAYER, PARENT


def cases() -> list[dict]:
    """Finite long/short, equality, cancellation, timing and late-boundary cases."""
    base = dict(
        open_=[9., 10.5, 12., 13., 12., 12., 13.],
        high=[10., 12., 14., 15., 19., 17., 20.],
        low=[8., 10.2, 11., 12., 10., 11., 12.],
        close=[9., 11., 13., 14., 11., 16., 19.],
        gap=[False] * 7, raw_side=[0] * 7, side=1, stop=7., limit=24,
    )
    result = []
    for side in (1, -1):
        for hazard in ("normal", "gap", "opposite", "stop", "level_equal", "break_equal", "rebreak_equal", "limit4", "limit5", "same_bar"):
            row = {k: v.copy() if isinstance(v, list) else v for k, v in base.items()}
            row["name"] = f"{side}:{hazard}"
            if hazard == "gap": row["gap"][5] = True
            if hazard == "opposite": row["raw_side"][5] = -1
            if hazard == "stop": row["low"][5] = 6.
            if hazard == "level_equal": row["close"][4] = 10.
            if hazard == "break_equal": row["close"][1] = row["low"][1] = 10.
            if hazard == "rebreak_equal": row["close"][5] = 15.
            if hazard.startswith("limit"): row["limit"] = int(hazard[5:])
            if hazard == "same_bar":
                row["low"][1] = 9.
                row["close"][4] = 18.
            if side == -1:
                # Positive-price reflection, preserving OHLC and tick geometry.
                row["open_"] = [30. - x for x in row["open_"]]
                row["close"] = [30. - x for x in row["close"]]
                row["high"], row["low"] = ([30. - x for x in row["low"]], [30. - x for x in row["high"]])
                row["raw_side"] = [-x for x in row["raw_side"]]
                row["stop"] = 30. - row["stop"]
                row["side"] = -1
            result.append(row)
    return result


def pine_array(values) -> str:
    return "array.from(" + ",".join("true" if x is True else "false" if x is False else repr(x) for x in values) + ")"


def build_core_probe() -> tuple[str, list[dict]]:
    """Use the independent frozen batch oracle to assert the native incremental core."""
    source = '//@version=6\nindicator("SPIKE V13.0 QA core", overlay=true)\n' + CORE.read_text()
    source += "\nvar int checks = 0\nif barstate.isfirst\n"
    expected = []
    for n, row in enumerate(cases()):
        oracle = confirmation(*(row[k] for k in ("open_", "high", "low", "close", "gap", "raw_side")), 0, row["side"], row["stop"], row["limit"])
        status = {"confirmed": 1, "expired": 2, "pending_boundary": 0}.get(oracle["status"])
        if status is None:
            status = {"gap": 3, "raw_opposite": 4, "stop_touch": 5, "lost_level": 6}[oracle["cancel_reason"]]
        expected.append(dict(name=row["name"], **oracle, code=status))
        for key in ("open_", "high", "low", "close", "gap", "raw_side"):
            typ = "bool" if key == "gap" else "int" if key == "raw_side" else "float"
            source += f"    array<{typ}> a{n}_{key} = {pine_array(row[key])}\n"
        source += f"    int s{n} = 0\n    float e{n} = na\n    int r{n} = 0\n    int d{n} = 0\n"
        level = row["high"][0] if row["side"] == 1 else row["low"][0]
        source += f"    for j = 1 to {len(row['close']) - 1}\n        if r{n} == 0\n"
        source += f"            [s, e, r] = f_v130_step(s{n}, e{n}, {row['side']}, {level}, {row['stop']}, j, array.get(a{n}_open_,j), array.get(a{n}_high,j), array.get(a{n}_low,j), array.get(a{n}_close,j), array.get(a{n}_gap,j), array.get(a{n}_raw_side,j), {row['limit']})\n"
        source += f"            s{n} := s\n            e{n} := e\n            r{n} := r\n            d{n} := j\n"
        source += f"    if r{n} != {status} or d{n} != {oracle['decision_i']}\n        runtime.error(\"core {row['name']} status/clock\")\n"
        if oracle["first_leg_extreme"] is not None:
            source += f"    if e{n} != {oracle['first_leg_extreme']}\n        runtime.error(\"core {row['name']} extreme\")\n"
        source += "    checks += 1\n"
    source += 'var table result = table.new(position.top_left,1,1)\nif barstate.islast\n    table.cell(result,0,0,"V13 core PASS " + str.tostring(checks) + "/20",text_color=color.teal)\nplot(checks,"QA cases",display=display.data_window)\n'
    return source, expected


def build_layer_probe() -> str:
    """Run exact queue/execution layer against independent hand-checkable paths."""
    parent = PARENT.read_text()
    risk = "f_risk(" + parent.split("f_risk(", 1)[1].split("\nf_path(", 1)[0]
    state = LAYER.read_text().split("float v130WaitingLevel", 1)[0]
    for name in ("open", "high", "low", "close", "time_close"):
        state = re.sub(r"\b" + name + r"\b", "qa_" + name, state)
    state = state.replace("syminfo.mintick", "0.01")
    setup = '''//@version=6
indicator("SPIKE V13.0 QA lifecycle", overlay=true)
int step = bar_index % 24
int scenario = int(math.floor(bar_index / 24)) % 6
bool enabled = bar_index >= 24
bool mirror = scenario == 1
var array<float> os = array.from(100.,101.5,103.,104.,107.,109.,114.,117.,132.,128.,128.,128.,128.,128.,128.,128.,100.,100.,100.,100.,100.,100.,100.,100.)
var array<float> hs = array.from(101.,104.,107.,120.,110.,115.,118.,134.,133.,129.,129.,129.,129.,129.,129.,129.,101.,101.,101.,101.,101.,101.,101.,101.)
var array<float> ls = array.from(99.,101.2,102.,101.,104.,108.,113.,116.,127.,127.,127.,127.,127.,127.,127.,127.,99.,99.,99.,99.,99.,99.,99.,99.)
var array<float> cs = array.from(100.,103.,106.,102.,108.,114.,117.,132.,128.,128.,128.,128.,128.,128.,128.,128.,100.,100.,100.,100.,100.,100.,100.,100.)
float oo = array.get(os,step)
float hh = array.get(hs,step)
float ll = array.get(ls,step)
float cc = array.get(cs,step)
// Scenario 2: stop on entry bar. 3: raw reverse at 6, exit at 7 open.
// 4: gap at 6 censors. 5: candidate confirmed while already occupied.
ll := scenario == 2 and step == 5 ? 97. : ll
if scenario == 5
    if step == 7
        oo := 117.
        hh := 119.
        ll := 114.
        cc := 117.
    if step == 8
        oo := 118.
        hh := 121.
        ll := 117.
        cc := 120.
float qa_open = mirror ? 200. - oo : oo
float qa_high = mirror ? 200. - ll : hh
float qa_low = mirror ? 200. - hh : ll
float qa_close = mirror ? 200. - cc : cc
int qa_time_close = time_close
bool dataGap = step == 16 or (scenario == 4 and step == 6)
bool pricesValid = true
float recentLow = step == 5 and scenario == 5 ? 101. : 99.
float recentHigh = 101.
float atr = 1.
const float riskFloor = 2.
const float stopBuffer = 0.2
const int stopLen = 5
const int V130_WAIT = 24
const float armR = 2.
const float trailAtr = 4.
const float roundTripCost = 0.002
bool v130Retest = enabled
bool v130ShowRisk = false
bool v130ShowLevels = false
bool v126Minimal = true
bool showLabels = false
bool showPanel = false
string v130Arm = "普通多空"
int signalSide = enabled and (step == 0 or (scenario == 5 and step == 5)) ? (mirror ? -1 : 1) : 0
int rawSignalSide = scenario == 3 and step == 6 ? -1 : signalSide
bool v11AnyJoint = false
color bear = color.red
color bull = color.teal
'''
    # f_risk must read the synthetic anchor close. Its args are explicit.
    checks = '''
var int checked = 0
if enabled and barstate.isconfirmed
    if step == 4 and (not v130Confirmed or v130ConfirmedSide != (mirror ? -1 : 1) or v130Side != 0)
        runtime.error("lifecycle confirmation clock / direction")
    if step == 5
        float expectedEntry = mirror ? 91. : 109.
        float expectedStop = mirror ? 102. : 98.
        if not v130Entered or math.abs(v130Entry - expectedEntry) > 0.0001 or math.abs(v130InitialStop - expectedStop) > 0.0001 or math.abs(v130Risk - 11.) > 0.0001
            runtime.error("lifecycle next-open / frozen stop / R")
        if scenario == 2 and (not v130Exited or v130ExitPrice != 98.)
            runtime.error("lifecycle entry-bar stop")
    if scenario == 3 and step == 6 and (v130Side != 1 or not v130ReversePending or v130Exited)
        runtime.error("lifecycle raw reverse scheduled")
    if scenario == 3 and step == 7 and (not v130Exited or v130ExitPrice != 117.)
        runtime.error("lifecycle raw reverse next open")
    if scenario == 4 and step == 6 and (not v130GapEnded or v130Exited or v130Side != 0 or array.size(v130Queue) != 0)
        runtime.error("lifecycle gap must censor")
    if (scenario == 0 or scenario == 1) and step == 7
        if not v130Armed or math.abs(v130Protection - (mirror ? 72. : 128.)) > 0.0001 or v130Exited
            runtime.error("lifecycle close trail effective next bar")
    if (scenario == 0 or scenario == 1) and step == 8
        float expected = (19. - 0.002 * (mirror ? 91. : 109.)) / 11.
        if not v130Exited or math.abs(v130ExitNetR - expected) > 0.0001
            runtime.error("lifecycle stop and exact net cost")
    if scenario == 5 and step == 8 and (v130Confirmed or not na(v130Scheduled) or v130Side != 1 or v130OccupiedCount <= nz(v130OccupiedCount[1]))
        runtime.error("lifecycle occupied confirmation must expire")
    checked += 1
var table result = table.new(position.top_left,1,1)
if barstate.islast
    table.cell(result,0,0,"V13 lifecycle PASS · " + str.tostring(checked) + " bars / 6 scenarios",text_color=color.teal)
plot(checked,"QA checked bars",display=display.data_window)
'''
    return setup + "\n" + risk + "\n" + CORE.read_text() + "\n" + state + checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    core, expected = build_core_probe()
    (args.output / "native_core_probe.pine").write_text(core)
    (args.output / "native_core_expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    (args.output / "native_lifecycle_probe.pine").write_text(build_layer_probe())
    print(f"Wrote synthetic Pine probes to {args.output}")


if __name__ == "__main__":
    main()
