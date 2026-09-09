"""Build synthetic Pine tests from the actual V2.7 risk helpers.

No market outcomes or parameter search are used. The NEAR example is an input
geometry fixture from the owner's 2026-09-09 16:30 Beijing release. All other
tuples are synthetic. Run the output in TradingView to execute Pine itself.
"""

from pathlib import Path
import argparse
import re

from yoyo.evaluation.build_imacd_v25_contract_probe import CASES as PRIOR_CASES


CASES = r'''
if barstate.isfirst
    [sl1, risk1, valid1] = f_initialRisk(1, 100, 99.9, 99.6, 2, 1, 0.01, true)
    f_check(valid1 and f_eq(sl1, 98) and f_eq(risk1, 2), "ATR floor / long")
    [sl2, risk2, valid2] = f_initialRisk(-1, 100, 100.1, 100.4, 2, 1, 0.01, true)
    f_check(valid2 and f_eq(sl2, 102) and f_eq(risk2, 2), "ATR floor / short")
    [sl3, risk3, valid3] = f_initialRisk(1, 100, 99, 95, 2, 1, 0.01, true)
    f_check(valid3 and f_eq(sl3, 95) and f_eq(risk3, 5), "structure wider than floor")
    [sl4, risk4, valid4] = f_initialRisk(1, 94, 99, 95, 2, 1, 0.01, true)
    f_check(not valid4, "gap through frozen structure cannot be rescued")
    [sl5, risk5, valid5] = f_initialRisk(-1, 106, 101, 105, 2, 1, 0.01, true)
    f_check(not valid5, "short gap through structure invalid")
    [sl6, risk6, valid6] = f_initialRisk(1, 95, 99, 95, 2, 1, 0.01, true)
    f_check(not valid6, "open exactly at frozen stop invalid")
    [sl7, risk7, valid7] = f_initialRisk(1, 2.443, 2.420, 2.320 - 0.2 * 0.03633996668, 0.03633996668, 1, 0.001, true)
    f_check(valid7 and f_eq(sl7, 2.312) and f_eq(risk7, 0.131), "NEAR structure + buffer / outward tick")
    [sl8, risk8, valid8] = f_initialRisk(1, 1, 0.9, 0.8, 2, 1, 0.01, true)
    f_check(not valid8, "nonpositive stop invalid")
    [sl9, risk9, valid9] = f_initialRisk(1, 100, 99, 95, 0, 1, 0.01, true)
    f_check(not valid9, "zero ATR invalid")
    [sl10, risk10, valid10] = f_initialRisk(1, 100, 99, 95, na, 1, 0.01, true)
    f_check(not valid10, "missing ATR invalid")
    [sl11, risk11, valid11] = f_initialRisk(1, 100, 99, na, 2, 1, 0.01, true)
    f_check(not valid11, "incomplete structure invalid")
    [sl12, risk12, valid12] = f_initialRisk(0, 100, 99, 95, 2, 1, 0.01, true)
    f_check(not valid12, "invalid direction")
    [sl13, risk13, valid13] = f_initialRisk(1, 100, 99, 95, 2, 1, 0, true)
    f_check(not valid13, "invalid tick")
    [sl14, risk14, valid14] = f_initialRisk(1, 100, 99, na, na, 1, 0.01, false)
    f_check(valid14 and f_eq(sl14, 99) and f_eq(risk14, 1), "legacy independent of ATR")
    [sl15, risk15, valid15] = f_initialRisk(1, 100, 100, 95, 2, 1, 0.01, false)
    f_check(not valid15, "legacy zero risk not invented")
    [sl16, risk16, valid16] = f_initialRisk(-1, 100, 100.1, 102.011, 1, 1, 0.01, true)
    f_check(valid16 and f_eq(sl16, 102.02), "short outward rounding")
    [sl17, risk17, valid17] = f_initialRisk(1, 100, 99.9, 97.999, 1, 1, 0.01, true)
    f_check(valid17 and f_eq(sl17, 97.99), "long outward rounding")
    [sl18, risk18, valid18] = f_initialRisk(1, 100, 100, 98, 1, 1, 0.01, true)
    f_check(valid18 and f_eq(risk18, 2), "flat signal candle with valid prior structure")
    [sl19, risk19, valid19] = f_initialRisk(1, 1.2, 1.13, na, na, 1, 0.01, false)
    f_check(valid19 and f_eq(sl19, 1.13), "legacy tick-aligned extreme not rounded twice")
    f_check(f_eq(f_rewardR(0, 3, true), 0), "trend mode has no invented target")
    f_check(f_eq(f_rewardR(0.5, 3, true), 0.5), "trend mode shows actual excursion below 3R")
    f_check(f_eq(f_rewardR(12, 3, true), 12), "trend mode uncapped above 10R")
    f_check(f_eq(f_rewardR(0.5, 3, false), 3), "legacy fixed reference retained")
    f_check(f_eq(f_rewardR(12, 3, false), 12), "legacy reference is not a TP execution")
'''


def build(source: Path, output: Path) -> int:
    source_text = source.read_text()
    helpers = []
    for version in ("2.5", "2.7"):
        helpers.append(source_text.split(f"// BEGIN V{version} PURE HELPERS\n", 1)[1].split(f"// END V{version} PURE HELPERS", 1)[0])
    prior = PRIOR_CASES.split("var table result =", 1)[0].replace("V2.5 contract", "V2.7 contract")
    count = len(re.findall(r"^    f_check\(", prior + CASES, re.M))
    footer = f'''\nvar table result = table.new(position.middle_center, 1, 1)
if barstate.islast
    table.cell(result, 0, 0, "V2.7 · {count} Pine contracts PASS\\nSynthetic engineering cases · no return estimate", text_color=color.teal)
plot(na, display=display.none)
'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('//@version=6\nindicator("IMACD V2.7 · engineering probe", overlay=false)\n' + "\n".join(helpers) + prior + CASES + footer)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("yoyo/evaluation/pine/imacd_dense_mtf_v2_7.pine"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Generated {build(args.source, args.output)} synthetic Pine checks")
