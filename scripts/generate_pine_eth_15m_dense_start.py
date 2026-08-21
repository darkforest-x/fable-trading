#!/usr/bin/env python3
"""Generate the selected causal ETH 15m V13 dense-start Pine paper arm.

The source is frozen V9.  This generator changes only the full-state signal
gate and adds the six-MA features frozen in the V13 preregistration.  Formation
features use ``[t-12,t-1]``; release features use the completed decision bar
``t``.  Stops, break-even, sizing, calendar guards, cooldown and costs are copied
unchanged.  The generator reads no market or holdout data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from yoyo.layers.l2_judgment.pine_cross_features import SIX_MA_DIRECTIONAL_PAIRS
from yoyo.layers.l2_judgment.pine_dense_start import DenseStartProfile


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = (
    PROJECT
    / "experiments/active/exp-pine-eth-15m-v1/pine/allin_eth_15m_v9_research.pine"
)
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-dense-start-v1"
PREREGISTRATION = EXPERIMENT / "preregistration.json"
PINE_DIR = EXPERIMENT / "pine"
MANIFEST = PINE_DIR / "dense_start_pine_manifest.json"

PYTHON_TO_PINE_MA = {
    "sma20": "ropeSma20",
    "ema20": "ropeEma20",
    "sma60": "ropeSma60",
    "ema60": "ropeEma60",
    "sma120": "ropeSma120",
    "ema120": "ropeEma120",
}
PINE_MAS = tuple(PYTHON_TO_PINE_MA.values())
PINE_UNORDERED_PAIRS = tuple(combinations(PINE_MAS, 2))
PINE_DIRECTIONAL_PAIRS = tuple(
    (PYTHON_TO_PINE_MA[fast], PYTHON_TO_PINE_MA[slow])
    for fast, slow in SIX_MA_DIRECTIONAL_PAIRS
)


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    """Replace one exact source block and fail on source drift."""

    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sum_terms(function: str, pairs: tuple[tuple[str, str], ...]) -> str:
    return " +\n".join(f"     {function}({left}, {right})" for left, right in pairs)


def _alignment_terms(operator: str) -> str:
    return " +\n".join(
        f"     ({fast} {operator} {slow} ? 1.0 : 0.0)"
        for fast, slow in PINE_DIRECTIONAL_PAIRS
    )


def _max6() -> str:
    a, b, c, d, e, f = PINE_MAS
    return f"math.max(math.max(math.max({a}, {b}), math.max({c}, {d})), math.max({e}, {f}))"


def _min6() -> str:
    a, b, c, d, e, f = PINE_MAS
    return f"math.min(math.min(math.min({a}, {b}), math.min({c}, {d})), math.min({e}, {f}))"


def _slope_terms(operator: str) -> str:
    return " +\n".join(
        f"     ({ma}SlopeAtr {operator} 0.0 ? 1.0 : 0.0)" for ma in PINE_MAS
    )


def _constants(profile: DenseStartProfile) -> str:
    return (
        "const int DENSE_WINDOW = 12\n"
        "const int DENSE_ATR_RELEASE_WINDOW = 8\n"
        "const int DENSE_SLOPE_LAG = 3\n"
        f"const int DENSE_MIN_PAIRWISE_CROSSES = {profile.min_pre_pairwise_crosses}\n"
        f"const float DENSE_MAX_BANDWIDTH_ATR_MEAN = {profile.max_pre_bandwidth_atr_mean:.10g}\n"
        f"const int DENSE_MIN_CURRENT_ALIGNMENT = {profile.min_current_alignment}\n"
        f"const int DENSE_MIN_PRE_CROSS_IMBALANCE = {profile.min_pre_cross_imbalance}\n"
        f"const float DENSE_MIN_SLOPE_COHERENCE = {profile.min_slope_coherence:.16g}\n"
        f"const float DENSE_MIN_ATR_RELEASE_RATIO = {profile.min_atr_release_ratio:.10g}\n"
    )


def _helpers() -> str:
    return (
        "f_crossAnyEvent(float left, float right) =>\n"
        "    (ta.crossover(left, right) or ta.crossunder(left, right)) ? 1.0 : 0.0\n\n"
        "f_crossUpEvent(float fast, float slow) =>\n"
        "    ta.crossover(fast, slow) ? 1.0 : 0.0\n\n"
        "f_crossDownEvent(float fast, float slow) =>\n"
        "    ta.crossunder(fast, slow) ? 1.0 : 0.0\n\n"
    )


def _feature_block(profile: DenseStartProfile) -> str:
    slope_definitions = "\n".join(
        f"float {ma}SlopeAtr = ({ma} - {ma}[DENSE_SLOPE_LAG]) / atr / DENSE_SLOPE_LAG"
        for ma in PINE_MAS
    )
    slope_mean_long = " + ".join(f"{ma}SlopeAtr" for ma in PINE_MAS)
    return (
        "float ropeSma20 = ta.sma(close, 20)\n"
        "float ropeEma20 = ta.ema(close, 20)\n"
        "float ropeSma60 = ta.sma(close, 60)\n"
        "float ropeEma60 = ta.ema(close, 60)\n"
        "float ropeSma120 = ta.sma(close, 120)\n"
        "float ropeEma120 = ta.ema(close, 120)\n"
        "float densePairwiseCrossEvents =\n"
        f"{_sum_terms('f_crossAnyEvent', PINE_UNORDERED_PAIRS)}\n"
        "float denseCrossUpEvents =\n"
        f"{_sum_terms('f_crossUpEvent', PINE_DIRECTIONAL_PAIRS)}\n"
        "float denseCrossDownEvents =\n"
        f"{_sum_terms('f_crossDownEvent', PINE_DIRECTIONAL_PAIRS)}\n"
        "// Formation excludes release bar t: source[1] makes the sum [t-12,t-1].\n"
        "float densePrePairwiseCrossCount = math.sum(densePairwiseCrossEvents[1], DENSE_WINDOW)\n"
        "float densePreCrossUpCount = math.sum(denseCrossUpEvents[1], DENSE_WINDOW)\n"
        "float densePreCrossDownCount = math.sum(denseCrossDownEvents[1], DENSE_WINDOW)\n"
        f"float denseRopeUpper = {_max6()}\n"
        f"float denseRopeLower = {_min6()}\n"
        "float denseBandwidthAtr = (denseRopeUpper - denseRopeLower) / atr\n"
        "float densePreBandwidthAtrMean = math.sum(denseBandwidthAtr[1], DENSE_WINDOW) / DENSE_WINDOW\n"
        "float denseLongAlignment =\n"
        f"{_alignment_terms('>=')}\n"
        "float denseShortAlignment =\n"
        f"{_alignment_terms('<=')}\n"
        f"{slope_definitions}\n"
        "float denseLongSlopeCoherence = (\n"
        f"{_slope_terms('>')}\n"
        "     ) / 6.0\n"
        "float denseShortSlopeCoherence = (\n"
        f"{_slope_terms('<')}\n"
        "     ) / 6.0\n"
        f"float denseLongMeanSlopeAtr = ({slope_mean_long}) / 6.0\n"
        "float denseShortMeanSlopeAtr = -denseLongMeanSlopeAtr\n"
        "float denseAtrReleaseRatio = atr / (math.sum(atr[1], DENSE_ATR_RELEASE_WINDOW) / DENSE_ATR_RELEASE_WINDOW)\n"
        "float denseLongCrossImbalance = densePreCrossUpCount - densePreCrossDownCount\n"
        "float denseShortCrossImbalance = -denseLongCrossImbalance\n"
        "bool denseReady = not na(ropeSma120) and not na(ropeEma120) and not na(densePrePairwiseCrossCount) and not na(densePreBandwidthAtrMean) and not na(denseAtrReleaseRatio) and not na(denseLongMeanSlopeAtr)\n"
        "bool denseLongRelease = close > denseRopeUpper and denseLongMeanSlopeAtr > 0.0 and (denseLongSlopeCoherence >= DENSE_MIN_SLOPE_COHERENCE or denseAtrReleaseRatio >= DENSE_MIN_ATR_RELEASE_RATIO)\n"
        "bool denseShortRelease = close < denseRopeLower and denseShortMeanSlopeAtr > 0.0 and (denseShortSlopeCoherence >= DENSE_MIN_SLOPE_COHERENCE or denseAtrReleaseRatio >= DENSE_MIN_ATR_RELEASE_RATIO)\n"
        "bool denseLongPass = denseReady and densePrePairwiseCrossCount >= DENSE_MIN_PAIRWISE_CROSSES and densePreBandwidthAtrMean <= DENSE_MAX_BANDWIDTH_ATR_MEAN and denseLongAlignment >= DENSE_MIN_CURRENT_ALIGNMENT and denseLongCrossImbalance >= DENSE_MIN_PRE_CROSS_IMBALANCE and denseLongRelease\n"
        "bool denseShortPass = denseReady and densePrePairwiseCrossCount >= DENSE_MIN_PAIRWISE_CROSSES and densePreBandwidthAtrMean <= DENSE_MAX_BANDWIDTH_ATR_MEAN and denseShortAlignment >= DENSE_MIN_CURRENT_ALIGNMENT and denseShortCrossImbalance >= DENSE_MIN_PRE_CROSS_IMBALANCE and denseShortRelease\n"
        f"// Selected preregistered profile: {profile.profile_id}.\n"
    )


def build_v13_dense_start(source: str, profile: DenseStartProfile) -> str:
    """Return one V13D full-state Pine script for the locked profile."""

    text = source.replace("V9", "V13D")
    text = replace_once(
        text,
        "// ALLIN ETH 15m V13D Research — frozen causal research candidate.\n"
        "// Research only: not TradingView-parity-approved, forward-eligible or production-eligible.",
        "// ALLIN ETH 15m V13D dense-start full gate — development-selected paper hypothesis.\n"
        "// Paper only: dense -> compression -> direction -> release gates the full state transition.",
        label="V13D header",
    )
    text = replace_once(
        text,
        '     "ALLIN ETH 15m V13D Research",\n',
        f'     "ALLIN ETH 15m V13D {profile.profile_id} Paper",\n',
        label="V13D strategy title",
    )
    text = replace_once(
        text,
        "const int SLOW_SLOPE_LAG = 12\n",
        "const int SLOW_SLOPE_LAG = 12\n" + _constants(profile),
        label="V13D constants",
    )
    text = replace_once(
        text,
        "float source = hl2\n",
        _helpers() + "float source = hl2\n",
        label="V13D helpers",
    )
    text = replace_once(
        text,
        "float atr = ta.atr(ATR_LEN)\n",
        "float atr = ta.atr(ATR_LEN)\n" + _feature_block(profile),
        label="V13D feature block",
    )
    text = replace_once(
        text,
        "bool rawSignal = rawLong or rawShort\n"
        "bool skippedSignal = rawSignal and tradesToSkip > 0\n"
        "if skippedSignal\n"
        "    tradesToSkip -= 1\n\n"
        "bool commonAllowed = barstate.isconfirmed and dateAllowed and timeAllowed and dayAllowed and volatilityAllowed and not skippedSignal\n"
        "bool longSignal = rawLong and commonAllowed\n"
        "bool shortSignal = rawShort and commonAllowed\n",
        "// Match V12F full-state semantics: a guarded rejection neither reverses\n"
        "// nor consumes cooldown; out-of-guard raw signals preserve V9 state.\n"
        "bool gateCandidateEligible = dateAllowed and timeAllowed and dayAllowed and volatilityAllowed\n"
        "bool gatedRawLong = rawLong and (not gateCandidateEligible or denseLongPass)\n"
        "bool gatedRawShort = rawShort and (not gateCandidateEligible or denseShortPass)\n"
        "bool rawSignal = gatedRawLong or gatedRawShort\n"
        "bool skippedSignal = rawSignal and tradesToSkip > 0\n"
        "if skippedSignal\n"
        "    tradesToSkip -= 1\n\n"
        "bool commonAllowed = barstate.isconfirmed and dateAllowed and timeAllowed and dayAllowed and volatilityAllowed and not skippedSignal\n"
        "bool longSignal = gatedRawLong and commonAllowed\n"
        "bool shortSignal = gatedRawShort and commonAllowed\n",
        label="V13D full-state gate",
    )
    text = text.replace("RESEARCH ONLY | ALLIN ETH 15m V13D", "PAPER ONLY | ALLIN ETH 15m V13D")
    text = text.replace("RESEARCH ONLY | V13D", "PAPER ONLY | V13D")
    text = text.replace("RESEARCH / PARITY REQUIRED", f"PAPER DENSE {profile.profile_id.upper()} / PARITY REQUIRED")
    return text


def load_profiles(path: Path = PREREGISTRATION) -> list[DenseStartProfile]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [DenseStartProfile.from_mapping(row) for row in payload["ordered_strictness_profiles"]]


def output_path(profile: DenseStartProfile) -> Path:
    return PINE_DIR / f"allin_eth_15m_v13d_dense_start_{profile.profile_id}_paper.pine"


def write_selected_pine(profile: DenseStartProfile) -> tuple[Path, dict[str, Any]]:
    """Write the selected Pine and a content-addressed paper manifest."""

    source = SOURCE.read_text(encoding="utf-8")
    path = output_path(profile)
    PINE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(build_v13_dense_start(source, profile), encoding="utf-8")
    manifest = {
        "artifact": "ETH 15m V13 dense-start Pine paper arm",
        "selected_profile": profile.profile_id,
        "source": str(SOURCE.relative_to(PROJECT)),
        "source_sha256": sha256(SOURCE),
        "output": str(path.relative_to(PROJECT)),
        "output_sha256": sha256(path),
        "formation_window": "[t-12,t-1]",
        "release_bar": "t",
        "entry_bar": "t+1 open",
        "future_feature_bars": 0,
        "bar_minutes": 15,
        "barrier_changed": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id", required=True)
    args = parser.parse_args()
    profiles = {profile.profile_id: profile for profile in load_profiles()}
    if args.profile_id not in profiles:
        raise SystemExit(f"unknown preregistered profile: {args.profile_id}")
    path, manifest = write_selected_pine(profiles[args.profile_id])
    print(json.dumps(manifest, indent=2))
    print(path)


if __name__ == "__main__":
    main()
