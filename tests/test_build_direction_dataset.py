from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from src.detection.build_direction_dataset import (
    LOOKBACK_BARS,
    build_direction_manifest,
    render_causal_image,
    select_manifest_rows,
    summarize_manifest,
)
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import HOLDOUT_START


def _synthetic_dense_frame(periods: int = 1100) -> pd.DataFrame:
    index = np.arange(periods)
    close = 100.0 + 0.08 * np.sin(index / 6) + 0.03 * np.sin(index / 19)
    open_ = close + 0.01 * np.sin(index / 3)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2025-11-01", periods=periods, freq="15min", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + 0.08,
            "low": np.minimum(open_, close) - 0.08,
            "close": close,
            "volume": 1_000.0 + 20.0 * np.cos(index / 11),
        }
    )


def test_build_direction_manifest_uses_real_causal_scanners_and_purge() -> None:
    frame = _synthetic_dense_frame()

    manifest = build_direction_manifest([("okx", "TEST_USDT_SWAP", frame)])

    assert not manifest.empty
    assert set(manifest["split"]) == {"train", "val"}
    assert set(manifest["direction_class"]) <= {"long", "short", "no_trade"}
    assert set(FEATURE_COLUMNS).issubset(manifest.columns)
    assert manifest["signal_time"].max() < HOLDOUT_START - pd.Timedelta(hours=18)
    assert manifest[["long_realized_ret", "short_realized_ret"]].notna().all().all()
    assert manifest.duplicated(["source", "symbol", "signal_time"]).sum() == 0


def test_render_causal_image_is_invariant_to_future_rows(tmp_path: Path) -> None:
    frame = _synthetic_dense_frame()
    changed = frame.copy()
    signal_i = 800
    changed.loc[signal_i + 1 :, ["open", "high", "low", "close"]] = 500.0
    baseline_enriched = add_indicators(frame)
    changed_enriched = add_indicators(changed)
    baseline_path = tmp_path / "baseline.png"
    changed_path = tmp_path / "changed.png"

    render_causal_image(baseline_enriched, signal_i=signal_i, out_path=baseline_path)
    render_causal_image(changed_enriched, signal_i=signal_i, out_path=changed_path)

    baseline = cv2.imread(str(baseline_path), cv2.IMREAD_UNCHANGED)
    mutated = cv2.imread(str(changed_path), cv2.IMREAD_UNCHANGED)
    assert baseline.shape == (742, 1280, 3)
    assert np.array_equal(baseline, mutated)


def test_select_and_summarize_manifest_reconcile_class_counts() -> None:
    rows = []
    for split in ("train", "val"):
        for class_name in ("long", "short", "no_trade"):
            for index in range(4):
                rows.append(
                    {
                        "split": split,
                        "direction_class": class_name,
                        "signal_time": pd.Timestamp("2026-01-01", tz="UTC")
                        + pd.Timedelta(minutes=index),
                    }
                )
    manifest = pd.DataFrame(rows)

    selected = select_manifest_rows(manifest, limit_per_class_split=2)
    summary = summarize_manifest(selected, lookback_bars=LOOKBACK_BARS)

    assert len(selected) == 12
    assert summary["images"] == 12
    assert summary["class_counts"] == {
        "train": {"long": 2, "no_trade": 2, "short": 2},
        "val": {"long": 2, "no_trade": 2, "short": 2},
    }
    assert summary["lookback_bars"] == 200
