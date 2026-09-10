"""Synthetic probe/temporal guards; native Pine execution is a separate receipt.

These tests do not claim to execute Pine or validate trading performance. They
protect source extraction, adversarial case geometry, independently hand-worked
expected values, and the integration guards outside the pure helpers.
"""

from decimal import Decimal
import hashlib
from pathlib import Path
import re

import pytest

from yoyo.evaluation.build_spike_burst_probe import (
    BEGIN, END, BURST_CASES, BURST_FIELDS, DEFAULT_SOURCE, LONG, SHORT,
    PATH_CASES, PRICE_CASES, PRICE_FIELDS, RISK_CASES, WINDOW_CASES,
    assertion_body, build, extract_helpers,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / DEFAULT_SOURCE


def _d(value):
    return Decimal(str(value))


def _ancestors(text, needle):
    """Inspect Pine indentation for the actual callsite, not helper declaration."""
    stack = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if needle in line:
            return [item[1] for item in stack]
        stack.append((indent, line.strip()))
    raise AssertionError(f"Missing required integration callsite: {needle}")


def test_probe_embeds_actual_helpers_exactly_and_source_hash(tmp_path):
    output = tmp_path / "probe.pine"
    count = build(SOURCE, output)
    text = SOURCE.read_text(encoding="utf-8")
    generated = output.read_text(encoding="utf-8")
    helpers = extract_helpers(text)
    assert generated.count(helpers) == 1
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() in generated
    assert count == 81
    assert len(re.findall(r"^    f_check\(", generated, re.M)) == count
    assert f"{count} Pine contracts PASS" in generated
    assert 'runtime.error("SPIKE Burst contract failed: "' in generated
    assert "if barstate.isfirst" in generated


@pytest.mark.parametrize("text", [
    "none", BEGIN + "body", END + BEGIN,
    BEGIN + "body" + END + BEGIN + "body" + END,
])
def test_ambiguous_or_missing_source_markers_fail_closed(text):
    with pytest.raises(ValueError):
        extract_helpers(text)


def test_builder_does_not_overwrite_indicator(tmp_path):
    source = tmp_path / "source.pine"
    original = BEGIN + "f_sample() => 1\n" + END
    source.write_text(original)
    with pytest.raises(ValueError, match="overwrite"):
        build(source, source)
    assert source.read_text() == original


def test_all_signal_negative_controls_change_exactly_one_input():
    failures = [case for case in BURST_CASES if not case.passes]
    assert len(failures) >= 20
    for case in failures:
        assert len(case.changes) == 1, case.name
        assert sum(a != b for a, b in zip(case.base, case.values)) == 1, case.name
    changed = {field for case in failures for field in case.changes}
    assert {"rv", "expansion", "o", "c", "zoneHigh", "ropeHigh", "previousMd", "sb", "h"} <= changed


def test_signal_gate_fixtures_have_independent_geometry_and_mirror():
    long = dict(zip(BURST_FIELDS, LONG))
    short = dict(zip(BURST_FIELDS, SHORT))
    assert (_d(long["c"]) - _d(long["o"])) / (_d(long["h"]) - _d(long["l"])) == _d(".65")
    assert (_d(long["c"]) - _d(long["l"])) / (_d(long["h"]) - _d(long["l"])) == _d(".75")
    for left, right in (("o", "o"), ("c", "c"), ("h", "l"), ("l", "h"),
                        ("zoneHigh", "zoneLow"), ("ropeHigh", "ropeLow"), ("ropeLow", "ropeHigh")):
        assert _d(long[left]) + _d(short[right]) == 200
    for field in ("md", "previousMd", "sb"):
        assert long[field] == -short[field]
    exact_body = next(case for case in BURST_CASES if case.name == "long exact body threshold")
    values = dict(zip(BURST_FIELDS, exact_body.values))
    assert (_d(values["c"]) - _d(values["o"])) / (_d(values["h"]) - _d(values["l"])) == _d(values["bodyGate"])


def test_price_first_zero_main_line_fixture_and_one_factor_negative_controls():
    assert len(PRICE_CASES) == 10
    positives = [case for case in PRICE_CASES if case.passes]
    assert len(positives) == 2
    for case in positives:
        values = dict(zip(PRICE_FIELDS, case.values))
        assert values["md"] == values["sb"] == 0
        assert values["side"] * (values["mi"] - values["priorMi"]) > 0
    for case in PRICE_CASES:
        if not case.passes:
            assert len(case.changes) == 1
            assert sum(a != b for a, b in zip(case.base, case.values)) == 1
    assert {next(iter(case.changes)) for case in PRICE_CASES if not case.passes} == {
        "priorMi", "md", "rv", "o", "c", "zoneHigh",
    }


def test_inclusive_six_bar_window_and_frozen_band_counterexample_exist():
    cases = {name: (args, expected) for name, args, expected in WINDOW_CASES}
    assert cases["release bar is age zero"][0][0] == 0
    assert cases["sixth bar included"][0][0] == 5
    assert cases["sixth bar included"][1] is True
    assert cases["seventh bar excluded"][0][0] == 6
    assert cases["seventh bar excluded"][1] is False
    frozen, frozen_passes = cases["frozen .10 keeps md .11 qualified"]
    drifting, drift_passes = cases["recomputed .13 would incorrectly cancel"]
    assert sum(a != b for a, b in zip(frozen, drifting)) == 1
    assert frozen[4] < frozen[3] < drifting[4]
    assert frozen_passes and not drift_passes


def test_valid_risk_expected_values_satisfy_independent_price_constraints():
    for name, args, expected in RISK_CASES:
        stop, risk, valid = expected
        if not valid:
            assert stop is None and risk is None
            continue
        side, entry, extreme, atr, floor_atr, buffer_atr, tick = map(_d, args)
        stop, risk = _d(stop), _d(risk)
        # Feasibility + nearest outward tick define the stop contract without a
        # duplicate min/max/floor implementation that could share the same bug.
        assert side * (entry - stop) == risk, name
        assert risk >= floor_atr * atr, name
        assert side * (extreme - side * buffer_atr * atr - stop) >= 0, name
        assert stop % tick == 0, name
        inward_tick = stop + side * tick
        assert (side * (entry - inward_tick) < floor_atr * atr
                or side * (extreme - side * buffer_atr * atr - inward_tick) < 0), name


def test_path_fixture_R_is_from_frozen_risk_and_stop_bar_cannot_add_peak():
    for name, args, expected in PATH_CASES:
        side, entry, risk, old_stop, old_peak, old_armed, o, h, l, c, atr, activation, distance, tick = args
        alive, stop, peak, current, exit_price, armed = expected
        price = c if alive else exit_price
        assert _d(current) == _d(side) * (_d(price) - _d(entry)) / _d(risk), name
        assert _d(side) * (_d(stop) - _d(old_stop)) >= 0, name
        if not alive:
            assert peak == old_peak, name
            assert stop == old_stop, name
            assert _d(side) * (_d(exit_price) - _d(old_stop)) <= 0, name
        elif old_armed:
            assert armed, name


def test_probe_chains_native_outputs_and_checks_prefix_replay():
    body, _ = assertion_body()
    assert "f_path(1, 100, 2, seqStop, seqPeak, seqArmed," in body
    assert '"native chained protection becomes operative next bar"' in body
    assert '"replaying identical prefix after future bar stays identical"' in body
    assert "seqA and not seqB" in body


def test_indicator_integrates_closed_bar_only_and_excludes_signal_bar_profit():
    text = SOURCE.read_text(encoding="utf-8")
    path_parents = _ancestors(text, "= f_path(trendSide")
    assert "if barstate.isconfirmed and ready" in path_parents
    assert "if trendSide != 0 and bar_index > entryBar" in path_parents
    burst_parents = _ancestors(text, "burstUp := side == 1")
    assert "if barstate.isconfirmed and ready" in burst_parents
    assert "if pendingSide != 0 and trendSide == 0 and not endedThisBar" in burst_parents
    assert "if burst" in burst_parents
    entry_tail = text.split("entryBar := bar_index", 1)[1]
    assert "peakR := 0.0" in entry_tail
    assert "currentR := riskValid ? 0.0 : na" in entry_tail
    assert 'alertcondition(burstUp, "SPIKE 多头爆发"' in text
    assert 'alertcondition(burstDown, "SPIKE 空头爆发"' in text
    assert "barcolor(burstUp ?" in text


def test_pending_episode_cannot_be_cancelled_by_recomputed_near_band():
    text = SOURCE.read_text(encoding="utf-8")
    assert "bool near = pendingSide == 0 and" in text
    assert "f_window(age, opportunity, pendingSide, md, launchBand," in text
    assert "launchBand := band" in text
    assert "launchHigh := quietHigh" in text
    assert "launchLow := quietLow" in text
    assert "pendingSide := 0" in text.split("if burst", 1)[1]


def test_price_first_uses_prior_box_before_current_bar_can_enlarge_it():
    text = SOURCE.read_text(encoding="utf-8")
    parents = _ancestors(text, "bool leadUp = canLead")
    assert "if barstate.isconfirmed and ready" in parents
    qualification = next(line for line in text.splitlines() if "bool canLead =" in line)
    for required in ("quietCount >= minQuiet", "pendingSide == 0", "trendSide == 0",
                     "not endedThisBar", "pastWidth <= denseWidth", "pastCrosses >= denseCrosses"):
        assert required in qualification
    assert "middle, middle[1], quietHigh, quietLow" in text
    assert text.index("bool leadUp = canLead") < text.index("quietHigh := quietCount == 1 ?")
    consumption = text.split("if leadingSide != 0", 1)[1].split("else if near", 1)[0]
    assert "launchHigh := quietHigh" in consumption
    assert "launchQuiet := quietCount" in consumption
    assert "quietCount := 0" in consumption
    assert "bool validWindow = leadingSide != 0 or f_window(" in text
    assert "bool burst = leadingSide != 0 or f_burst(" in text


def test_signal_features_are_past_only_and_plots_do_not_backstamp():
    text = SOURCE.read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
    assert not re.search(r"\b(?:request\.security|security|ta\.pivothigh|ta\.pivotlow)\s*\(", code)
    assert not re.search(r"\bvarip\b|lookahead_on|offset\s*=\s*-", code)
    assert "ta.median(volume[1], 20)" in code
    assert "ta.tr(true) / atr[1]" in code
    assert "ta.sma(width[1], denseLen)" in code
    assert "math.sum(flips[1], denseLen)" in code
    assert "zone := box.new(bar_index, quietHigh, bar_index, quietLow," in code
    assert "label.new(bar_index, md," in code
    assert "barstate.isconfirmed ? md : md[1]" in code
    assert "barstate.isconfirmed ? sb : sb[1]" in code


def test_protection_plot_uses_pre_update_stop_and_zero_axis_retained():
    text = SOURCE.read_text(encoding="utf-8")
    freeze = text.index("float visibleProtection = trendSide != 0 ? protection : na")
    mutation = text.index("protection := newProtection")
    assert freeze < mutation
    assert 'plot(showRisk ? visibleProtection : na, "本根有效保护"' in text
    assert 'hline(0, "零轴"' in text
    assert "plot.style_histogram" not in text
