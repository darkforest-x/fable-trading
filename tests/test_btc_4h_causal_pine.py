"""Static and arithmetic contracts for the causal 4h Pine indicator.

These tests deliberately do not load candle data. They verify that the Pine
source remains a current-bar research indicator whose constants match the
preregistered causal-prefix contract. Historical or holdout evaluation is a
separate Owner decision.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
EXPERIMENT = REPO / "experiments/active/exp-btc-4h-causal-pine-v1"
PREREG = EXPERIMENT / "preregistration.json"
PINE = EXPERIMENT / "pine/fable_4h_ma_launch_causal_v1.pine"


def _prereg() -> dict:
    return json.loads(PREREG.read_text())


def _source() -> str:
    return PINE.read_text()


def _code_without_line_comments() -> str:
    return "\n".join(line.split("//", 1)[0] for line in _source().splitlines())


def _pine_float_constant(name: str) -> float:
    match = re.search(
        rf"^const float {re.escape(name)} = ([0-9]+(?:\.[0-9]+)?)$",
        _source(),
        flags=re.MULTILINE,
    )
    assert match, f"missing Pine constant {name}"
    return float(match.group(1))


def test_preregistration_is_explicitly_causal_and_holdout_free() -> None:
    prereg = _prereg()
    causal = prereg["causal_contract"]
    assert causal["timeframe_seconds"] == 14_400
    assert causal["candidate_anchor_lags"] == [0, 1, 2]
    assert causal["plot_offset"] == 0
    assert causal["future_bars"] == 0
    assert causal["orders"] is False
    assert prereg["holdout"] == {
        "read": False,
        "consumption_number": 0,
        "post_compile_rule": (
            "Do not inspect or score historical/holdout signals in this task. "
            "Any evaluation or semantic threshold change needs an explicit Owner "
            "decision and a separately recorded configuration."
        ),
    }
    assert prereg["safety"]["training_eligible"] is False
    assert prereg["safety"]["production_eligible"] is False


def test_pine_is_an_indicator_with_no_order_or_external_data_surface() -> None:
    code = _code_without_line_comments()
    assert _source().startswith("//@version=6\n")
    assert re.search(r"\bindicator\(", code)
    assert not re.search(r"\bstrategy\(", code)
    assert "strategy." not in code
    assert "request." not in code
    assert "lookahead" not in code.lower()
    assert not re.search(r"(?<!condition)\balert\s*\(", code)
    assert code.count("alertcondition(") == 2


def test_every_signal_is_current_bar_confirmed_and_never_back_plotted() -> None:
    code = _code_without_line_comments()
    assert "bool longSignal = barstate.isconfirmed" in code
    assert "bool shortSignal = barstate.isconfirmed" in code
    assert "timeframe.in_seconds() == TIMEFRAME_SECONDS" in code
    assert "TIMEFRAME_SECONDS = 14400" in code
    assert not re.search(r"\[\s*-\s*\d+\s*\]", code)
    assert not re.search(r"\boffset\s*=", code)
    offsets = {int(value) for value in re.findall(r"\[(\d+)\]", code)}
    assert offsets == {1, 2, 3}


def test_only_tip_through_tip_minus_two_candidates_exist() -> None:
    source = _source()
    for side in ("long", "short"):
        for lag, observed in enumerate((1, 2, 3)):
            assert re.search(
                rf"bool {side}Pass{lag} = f_candidatePass\([^\n]+, {observed},",
                source,
            )
        assert f"{side}Pass3" not in source
        assert f"{side}Score3" not in source
    assert "No fourth or later bar is accessed." in source


def test_frozen_pine_constants_match_preregistration() -> None:
    gates = _prereg()["frozen_gates"]
    mapping = {
        "MA_SPREAD_BEFORE_MAX_PCT": "ma_spread_before_max_pct",
        "ANCHOR_TO_BUNDLE_MAX_PCT": "anchor_to_bundle_max_pct",
        "PRE_RANGE_MAX_PCT": "pre_range_max_pct",
        "FIRST3_CLOSE_MIN_PCT": "first3_close_min_pct",
        "RELEASE12_CLOSE_MIN_PCT": "release12_close_min_pct",
        "RELEASE12_FAVORABLE_ATR_MIN": "release12_favorable_atr_min",
    }
    for pine_name, prereg_name in mapping.items():
        assert _pine_float_constant(pine_name) == pytest.approx(gates[prereg_name])


def test_early_floors_are_monotonic_pro_rata_prefixes() -> None:
    gates = _prereg()["frozen_gates"]
    per_bar_move = max(
        gates["first3_close_min_pct"] / 3.0,
        gates["release12_close_min_pct"] / 12.0,
    )
    per_bar_atr = gates["release12_favorable_atr_min"] / 12.0
    move_floors = [bars * per_bar_move for bars in (1, 2, 3)]
    atr_floors = [bars * per_bar_atr for bars in (1, 2, 3)]
    assert move_floors == sorted(move_floors)
    assert atr_floors == sorted(atr_floors)
    assert move_floors == pytest.approx(
        [0.3632539295506435, 0.726507859101287, 1.0897617886519304]
    )
    assert atr_floors == pytest.approx(
        [0.4205466230279153, 0.8410932460558306, 1.261639869083746]
    )


def test_long_short_release_arithmetic_is_an_exact_sign_mirror() -> None:
    anchor_open = 100.0
    current_close = 103.0
    release_high = 105.0
    release_low = 96.0
    atr_before = 2.0

    long_move = 100.0 * math.log(current_close / anchor_open)
    short_move = -100.0 * math.log(current_close / anchor_open)
    assert short_move == pytest.approx(-long_move)

    long_favorable = (release_high - anchor_open) / atr_before
    short_favorable = (anchor_open - release_low) / atr_before
    assert long_favorable == pytest.approx(2.5)
    assert short_favorable == pytest.approx(2.0)

    source = _source()
    assert "direction * 100.0 * math.log(currentClose / anchorOpen)" in source
    assert "direction == 1 ?" in source
    assert "(releaseHigh - anchorOpen) / atrBefore" in source
    assert "(anchorOpen - releaseLow) / atrBefore" in source


def test_diagnostic_score_is_not_a_numeric_admission_threshold() -> None:
    code = _code_without_line_comments()
    assert "diagnostic score" in _source().lower()
    assert not re.search(
        r"(?:long|short)(?:Best)?Score\s*(?:>=|>|<=|<)\s*[0-9]",
        code,
        flags=re.IGNORECASE,
    )
    assert "bool longRaw = timeframeOk and longSideAllowed and not na(longBestScore)" in code
    assert "bool shortRaw = timeframeOk and shortSideAllowed and not na(shortBestScore)" in code
