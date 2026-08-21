#!/usr/bin/env python3
"""Generate the selected ETH 15m V15E soft trend-ensemble Pine paper arm.

The frozen V12F source remains authoritative for signals, W8 state gating,
stops, break-even, sizing, cooldown and costs.  This generator adds only the
preregistered causal multi-speed EWMAC/Donchian quality score and the 20%
six-MA dense soft contribution.  It reads no market data or holdout rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.generate_pine_eth_15m_dense_start import (
    PINE_MAS,
    PINE_UNORDERED_PAIRS,
    PROJECT,
    _alignment_terms,
    _max6,
    _min6,
    _slope_terms,
    _sum_terms,
    replace_once,
)
from yoyo.layers.l2_judgment.pine_trend_ensemble import TrendEnsembleProfile


SOURCE = (
    PROJECT
    / "experiments/active/exp-pine-eth-15m-v1/pine/allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine"
)
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-trend-ensemble-v1"
PREREGISTRATION = EXPERIMENT / "preregistration.json"
PINE_DIR = EXPERIMENT / "pine"
MANIFEST = PINE_DIR / "trend_ensemble_pine_manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _constants(profile: TrendEnsembleProfile) -> str:
    return (
        "const int TREND_EWMAC_FAST_1 = 8\n"
        "const int TREND_EWMAC_SLOW_1 = 32\n"
        "const int TREND_EWMAC_FAST_2 = 16\n"
        "const int TREND_EWMAC_SLOW_2 = 64\n"
        "const int TREND_EWMAC_FAST_3 = 32\n"
        "const int TREND_EWMAC_SLOW_3 = 128\n"
        "const int TREND_DONCHIAN_1 = 24\n"
        "const int TREND_DONCHIAN_2 = 48\n"
        "const int TREND_DONCHIAN_3 = 96\n"
        "const float TREND_EWMAC_SATURATION_ATR = 2.0\n"
        "const float TREND_WEIGHT = 0.80\n"
        "const float TREND_DENSE_WEIGHT = 0.20\n"
        f"const float TREND_MIN_QUALITY = {profile.minimum_quality:.10g}\n"
        "const int DENSE_WINDOW = 12\n"
        "const int DENSE_ATR_RELEASE_WINDOW = 8\n"
        "const int DENSE_SLOPE_LAG = 3\n"
    )


def _helpers() -> str:
    return (
        "\nf_crossAnyEvent(float left, float right) =>\n"
        "    (ta.crossover(left, right) or ta.crossunder(left, right)) ? 1.0 : 0.0\n\n"
        "f_clipUnit(float value) =>\n"
        "    math.max(-1.0, math.min(1.0, value))\n\n"
        "f_clip01(float value) =>\n"
        "    math.max(0.0, math.min(1.0, value))\n\n"
        "f_priorChannelPosition(float value, float upper, float lower) =>\n"
        "    float halfRange = (upper - lower) / 2.0\n"
        "    halfRange > 0.0 ? f_clipUnit((value - (upper + lower) / 2.0) / halfRange) : na\n"
    )


def _feature_block() -> str:
    slope_definitions = "\n".join(
        f"float {ma}SlopeAtr = ({ma} - {ma}[DENSE_SLOPE_LAG]) / atr / DENSE_SLOPE_LAG"
        for ma in PINE_MAS
    )
    slope_mean = " + ".join(f"{ma}SlopeAtr" for ma in PINE_MAS)
    return (
        "\n// V15E causal six-MA soft component; formation ends at t-1.\n"
        "float densePairwiseCrossEvents =\n"
        f"{_sum_terms('f_crossAnyEvent', PINE_UNORDERED_PAIRS)}\n"
        "float densePrePairwiseCrossCount = math.sum(densePairwiseCrossEvents[1], DENSE_WINDOW)\n"
        "float densePreCrossUpCount = math.sum(sixMaCrossUpEvents[1], DENSE_WINDOW)\n"
        "float densePreCrossDownCount = math.sum(sixMaCrossDownEvents[1], DENSE_WINDOW)\n"
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
        f"float denseLongMeanSlopeAtr = ({slope_mean}) / 6.0\n"
        "float denseShortMeanSlopeAtr = -denseLongMeanSlopeAtr\n"
        "float denseAtrReleaseRatio = atr / (math.sum(atr[1], DENSE_ATR_RELEASE_WINDOW) / DENSE_ATR_RELEASE_WINDOW)\n"
        "float denseCrossChurn = densePreCrossUpCount + densePreCrossDownCount\n"
        "float denseLongDirectionalShare = denseCrossChurn > 0.0 ? densePreCrossUpCount / denseCrossChurn : 0.0\n"
        "float denseShortDirectionalShare = denseCrossChurn > 0.0 ? densePreCrossDownCount / denseCrossChurn : 0.0\n"
        "float denseDensityScore = f_clip01(densePrePairwiseCrossCount / 4.0)\n"
        "float denseCompressionScore = f_clip01(1.0 - densePreBandwidthAtrMean / 4.0)\n"
        "float denseLongDirectionScore = f_clip01((denseLongAlignment / 12.0 + denseLongDirectionalShare) / 2.0)\n"
        "float denseShortDirectionScore = f_clip01((denseShortAlignment / 12.0 + denseShortDirectionalShare) / 2.0)\n"
        "float denseAtrReleaseScore = f_clip01((denseAtrReleaseRatio - 0.8) / 0.4)\n"
        "float denseLongSlopeScore = f_clip01(denseLongMeanSlopeAtr / 0.10)\n"
        "float denseShortSlopeScore = f_clip01(denseShortMeanSlopeAtr / 0.10)\n"
        "float denseLongReleaseScore = ((close > denseRopeUpper ? 1.0 : 0.0) + denseLongSlopeCoherence + denseLongSlopeScore + denseAtrReleaseScore) / 4.0\n"
        "float denseShortReleaseScore = ((close < denseRopeLower ? 1.0 : 0.0) + denseShortSlopeCoherence + denseShortSlopeScore + denseAtrReleaseScore) / 4.0\n"
        "float denseLongScore = (denseDensityScore + denseCompressionScore + denseLongDirectionScore + denseLongReleaseScore) / 4.0\n"
        "float denseShortScore = (denseDensityScore + denseCompressionScore + denseShortDirectionScore + denseShortReleaseScore) / 4.0\n"
        "bool denseScoreReady = not na(densePrePairwiseCrossCount) and not na(densePreBandwidthAtrMean) and not na(denseLongMeanSlopeAtr) and not na(denseAtrReleaseRatio)\n\n"
        "// Industry-style multi-speed EWMAC and prior-channel Donchian forecasts.\n"
        "float trendEwmac1 = math.tanh(((ta.ema(close, TREND_EWMAC_FAST_1) - ta.ema(close, TREND_EWMAC_SLOW_1)) / atr) / TREND_EWMAC_SATURATION_ATR)\n"
        "float trendEwmac2 = math.tanh(((ta.ema(close, TREND_EWMAC_FAST_2) - ta.ema(close, TREND_EWMAC_SLOW_2)) / atr) / TREND_EWMAC_SATURATION_ATR)\n"
        "float trendEwmac3 = math.tanh(((ta.ema(close, TREND_EWMAC_FAST_3) - ta.ema(close, TREND_EWMAC_SLOW_3)) / atr) / TREND_EWMAC_SATURATION_ATR)\n"
        "float trendDonchian1 = f_priorChannelPosition(close, ta.highest(high[1], TREND_DONCHIAN_1), ta.lowest(low[1], TREND_DONCHIAN_1))\n"
        "float trendDonchian2 = f_priorChannelPosition(close, ta.highest(high[1], TREND_DONCHIAN_2), ta.lowest(low[1], TREND_DONCHIAN_2))\n"
        "float trendDonchian3 = f_priorChannelPosition(close, ta.highest(high[1], TREND_DONCHIAN_3), ta.lowest(low[1], TREND_DONCHIAN_3))\n"
        "float trendForecast = (trendEwmac1 + trendEwmac2 + trendEwmac3 + trendDonchian1 + trendDonchian2 + trendDonchian3) / 6.0\n"
        "float trendLongSupport = f_clip01((1.0 + trendForecast) / 2.0)\n"
        "float trendShortSupport = f_clip01((1.0 - trendForecast) / 2.0)\n"
        "float trendLongQuality = TREND_WEIGHT * trendLongSupport + TREND_DENSE_WEIGHT * denseLongScore\n"
        "float trendShortQuality = TREND_WEIGHT * trendShortSupport + TREND_DENSE_WEIGHT * denseShortScore\n"
        "bool trendReady = denseScoreReady and not na(trendForecast) and not na(trendLongQuality) and not na(trendShortQuality)\n"
        "bool trendLongPass = trendReady and trendLongQuality >= TREND_MIN_QUALITY\n"
        "bool trendShortPass = trendReady and trendShortQuality >= TREND_MIN_QUALITY\n"
        "bool v15LongPass = sixMaLongPass and trendLongPass\n"
        "bool v15ShortPass = sixMaShortPass and trendShortPass\n"
    )


def build_v15_trend_ensemble(source: str, profile: TrendEnsembleProfile) -> str:
    """Return one deterministic V15E Pine script for a locked profile."""

    text = source.replace("V12F", "V15E")
    text = replace_once(
        text,
        "// ALLIN ETH 15m V15E MA6-W8 full gate — post-selection paper hypothesis.\n"
        "// Paper only: W8 gates entries, reversals and guarded cooldown transitions.",
        "// ALLIN ETH 15m V15E multi-speed soft trend ensemble — development-selected paper hypothesis.\n"
        "// Paper only: V12F candidates plus 80% trend support and 20% six-MA soft quality.",
        label="V15E header",
    )
    text = replace_once(
        text,
        '     "ALLIN ETH 15m V15E Paper",\n',
        f'     "ALLIN ETH 15m V15E {profile.profile_id} Paper",\n',
        label="V15E title",
    )
    text = replace_once(
        text,
        "const float SIX_MA_CROSS_THRESHOLD = 0.0\n",
        "const float SIX_MA_CROSS_THRESHOLD = 0.0\n" + _constants(profile),
        label="V15E constants",
    )
    text = replace_once(
        text,
        "f_crossDownEvent(float fast, float slow) =>\n"
        "    ta.crossunder(fast, slow) ? 1.0 : 0.0\n",
        "f_crossDownEvent(float fast, float slow) =>\n"
        "    ta.crossunder(fast, slow) ? 1.0 : 0.0\n" + _helpers(),
        label="V15E helpers",
    )
    text = replace_once(
        text,
        "bool sixMaShortPass = sixMaReady and sixMaCrossImbalanceShortW8 >= SIX_MA_CROSS_THRESHOLD\n",
        "bool sixMaShortPass = sixMaReady and sixMaCrossImbalanceShortW8 >= SIX_MA_CROSS_THRESHOLD\n"
        + _feature_block(),
        label="V15E feature block",
    )
    text = replace_once(
        text,
        "// The historical dynamic gate scores only guarded candidates. A raw\n"
        "// signal outside those guards must still consume cooldown like V9,\n"
        "// while a guarded W8 rejection suppresses the entire state transition.\n"
        "bool gateCandidateEligible = dateAllowed and timeAllowed and dayAllowed and volatilityAllowed\n"
        "bool gatedRawLong = rawLong and (not gateCandidateEligible or sixMaLongPass)\n"
        "bool gatedRawShort = rawShort and (not gateCandidateEligible or sixMaShortPass)\n",
        "// Only guarded V12F candidates receive the soft quality threshold.\n"
        "// Out-of-guard raw signals preserve the frozen cooldown state contract.\n"
        "bool gateCandidateEligible = dateAllowed and timeAllowed and dayAllowed and volatilityAllowed\n"
        "bool gatedRawLong = rawLong and (not gateCandidateEligible or v15LongPass)\n"
        "bool gatedRawShort = rawShort and (not gateCandidateEligible or v15ShortPass)\n",
        label="V15E full-state integration",
    )
    text = text.replace(
        "PAPER MA6-W8 FULL / PARITY REQUIRED",
        f"PAPER TREND {profile.profile_id.upper()} / PARITY REQUIRED",
        1,
    )
    return text


def load_profiles(path: Path = PREREGISTRATION) -> list[TrendEnsembleProfile]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        TrendEnsembleProfile.from_mapping(row)
        for row in payload["ordered_threshold_profiles"]
    ]


def output_path(profile: TrendEnsembleProfile) -> Path:
    return PINE_DIR / f"allin_eth_15m_v15e_trend_ensemble_{profile.profile_id}_paper.pine"


def write_selected_pine(profile: TrendEnsembleProfile) -> tuple[Path, dict[str, Any]]:
    """Write the selected paper Pine and content-addressed manifest."""

    PINE_DIR.mkdir(parents=True, exist_ok=True)
    path = output_path(profile)
    path.write_text(
        build_v15_trend_ensemble(SOURCE.read_text(encoding="utf-8"), profile),
        encoding="utf-8",
    )
    manifest = {
        "artifact": "ETH 15m V15E multi-speed soft trend-ensemble Pine paper arm",
        "selected_profile": profile.profile_id,
        "minimum_quality": profile.minimum_quality,
        "source": str(SOURCE.relative_to(PROJECT)),
        "source_sha256": _sha256(SOURCE),
        "output": str(path.relative_to(PROJECT)),
        "output_sha256": _sha256(path),
        "ewmac_speed_pairs": [[8, 32], [16, 64], [32, 128]],
        "donchian_windows": [24, 48, 96],
        "trend_weight": 0.80,
        "dense_weight": 0.20,
        "decision_bar": "t",
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
