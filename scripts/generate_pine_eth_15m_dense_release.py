#!/usr/bin/env python3
"""Generate the fixed ETH 15m V14R literal-release Pine paper arm."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.generate_pine_eth_15m_dense_start import (
    PROJECT,
    SOURCE,
    build_v13_dense_start,
    load_profiles,
)
from yoyo.layers.l2_judgment.pine_dense_start import DenseStartProfile


EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-dense-release-v2"
PINE_DIR = EXPERIMENT / "pine"
OUTPUT = PINE_DIR / "allin_eth_15m_v14r_dense_release_paper.pine"
MANIFEST = PINE_DIR / "dense_release_pine_manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_v14_dense_release(source: str, profile: DenseStartProfile) -> str:
    """Return V13 setup semantics with only the release boolean corrected."""

    text = build_v13_dense_start(source, profile).replace("V13D", "V14R")
    text = text.replace(
        "const float DENSE_MIN_ATR_RELEASE_RATIO = 1\n",
        "const float DENSE_MIN_ATR_RELEASE_RATIO = 1\n"
        "const float DENSE_MIN_TRUE_RANGE_ATR_RATIO = 1.0\n"
        "const float DENSE_MIN_BREAKOUT_EXPANSION_ATR = 0.0\n",
        1,
    )
    marker = (
        "float denseLongCrossImbalance = densePreCrossUpCount - densePreCrossDownCount\n"
    )
    feature_block = (
        "float denseTrueRange = math.max(high - low, math.max(math.abs(high - close[1]), math.abs(low - close[1])))\n"
        "float denseTrueRangeAtrRatio = denseTrueRange / atr[1]\n"
        "float denseLongBreakoutDistanceAtr = (close - denseRopeUpper) / atr\n"
        "float denseShortBreakoutDistanceAtr = (denseRopeLower - close) / atr\n"
        "float densePriorLongDistanceAtr = (close[1] - denseRopeUpper[1]) / atr[1]\n"
        "float densePriorShortDistanceAtr = (denseRopeLower[1] - close[1]) / atr[1]\n"
        "float denseLongBreakoutExpansionAtr = denseLongBreakoutDistanceAtr - densePriorLongDistanceAtr\n"
        "float denseShortBreakoutExpansionAtr = denseShortBreakoutDistanceAtr - densePriorShortDistanceAtr\n"
    )
    if text.count(marker) != 1:
        raise RuntimeError("V14R feature insertion marker drifted")
    text = text.replace(marker, feature_block + marker, 1)
    old = (
        "bool denseLongRelease = close > denseRopeUpper and denseLongMeanSlopeAtr > 0.0 and (denseLongSlopeCoherence >= DENSE_MIN_SLOPE_COHERENCE or denseAtrReleaseRatio >= DENSE_MIN_ATR_RELEASE_RATIO)\n"
        "bool denseShortRelease = close < denseRopeLower and denseShortMeanSlopeAtr > 0.0 and (denseShortSlopeCoherence >= DENSE_MIN_SLOPE_COHERENCE or denseAtrReleaseRatio >= DENSE_MIN_ATR_RELEASE_RATIO)\n"
    )
    new = (
        "// Literal confirmed-bar release: true range expands and price moves farther outside the rope.\n"
        "bool denseLongRelease = close > denseRopeUpper and denseLongMeanSlopeAtr > 0.0 and denseLongSlopeCoherence >= DENSE_MIN_SLOPE_COHERENCE and denseTrueRangeAtrRatio >= DENSE_MIN_TRUE_RANGE_ATR_RATIO and denseLongBreakoutExpansionAtr > DENSE_MIN_BREAKOUT_EXPANSION_ATR\n"
        "bool denseShortRelease = close < denseRopeLower and denseShortMeanSlopeAtr > 0.0 and denseShortSlopeCoherence >= DENSE_MIN_SLOPE_COHERENCE and denseTrueRangeAtrRatio >= DENSE_MIN_TRUE_RANGE_ATR_RATIO and denseShortBreakoutExpansionAtr > DENSE_MIN_BREAKOUT_EXPANSION_ATR\n"
    )
    if text.count(old) != 1:
        raise RuntimeError("V14R release source drifted")
    text = text.replace(old, new, 1)
    text = text.replace(
        "dense-start full gate — development-selected paper hypothesis",
        "dense-release correction — fixed post-review paper hypothesis",
        1,
    )
    text = text.replace(
        "dense -> compression -> direction -> release gates the full state transition",
        "V13 setup plus literal TR/distance release gates the full state transition",
        1,
    )
    return text


def selected_profile() -> DenseStartProfile:
    return next(profile for profile in load_profiles() if profile.profile_id == "dense_l1")


def write_pine() -> tuple[Path, dict[str, Any]]:
    profile = selected_profile()
    PINE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        build_v14_dense_release(SOURCE.read_text(encoding="utf-8"), profile),
        encoding="utf-8",
    )
    manifest = {
        "artifact": "ETH 15m V14R literal dense-release Pine paper arm",
        "base_profile": profile.profile_id,
        "source": str(SOURCE.relative_to(PROJECT)),
        "source_sha256": _sha256(SOURCE),
        "output": str(OUTPUT.relative_to(PROJECT)),
        "output_sha256": _sha256(OUTPUT),
        "minimum_true_range_atr_ratio": 1.0,
        "minimum_breakout_expansion_atr": 0.0,
        "bar_minutes": 15,
        "future_feature_bars": 0,
        "barrier_changed": False,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return OUTPUT, manifest


def main() -> None:
    path, manifest = write_pine()
    print(json.dumps(manifest, indent=2))
    print(path)


if __name__ == "__main__":
    main()
