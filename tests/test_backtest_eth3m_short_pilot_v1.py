from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.backtest_eth3m_short_pilot_v1 import (
    FUTURE_BARS,
    ROUND_TRIP_COST,
    WINDOW,
    assign_atr_buckets,
    build_eligible,
    dedupe_fire_indices,
    matched_controls,
    scan_fingerprint,
    short_hold_outcome,
)


def _frame(n: int = 900) -> pd.DataFrame:
    t = pd.date_range("2026-03-01", periods=n, freq="3min", tz="UTC")
    price = 2000.0 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "open_time": t,
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price + 1,
            "volume": 1.0,
            "atr_pct": 0.001 + np.arange(n) * 1e-7,
        }
    )


def test_build_eligible_has_no_training_pixel_overlap() -> None:
    frame = _frame()
    manifest = pd.DataFrame(
        {
            "sample_id": ["a"],
            "split": ["train"],
            "causal_start_time": [frame.open_time.iloc[300]],
            "anchor_time": [frame.open_time.iloc[499]],
        }
    )
    got = build_eligible(frame, manifest)
    assert (got.train_pixel_overlap_bars == 0).all()
    for i in got.bar_i.astype(int):
        assert not (i - WINDOW + 1 <= 499 and i >= 300)
    assert got.loc[got.strict_oos, "bar_i"].min() >= 499 + WINDOW
    assert (got.bar_i + FUTURE_BARS < len(frame)).all()


def test_short_hold_outcome_uses_next_open_and_sixty_bar_close() -> None:
    frame = _frame(200)
    signal_i = 20
    result = short_hold_outcome(frame, signal_i)
    entry = frame.open.iloc[signal_i + 1]
    exit_close = frame.close.iloc[signal_i + FUTURE_BARS]
    expected = 1 - exit_close / entry
    assert result["entry_i"] == signal_i + 1
    assert result["exit_i"] == signal_i + FUTURE_BARS
    assert np.isclose(result["gross_ret_3h"], expected)
    assert np.isclose(result["net_ret_3h"], expected - ROUND_TRIP_COST)


def test_dedupe_uses_absolute_bar_gap() -> None:
    assert dedupe_fire_indices([100, 101, 117, 118, 119, 136]) == [100, 118, 136]


def test_matched_controls_never_cross_run_or_atr_bucket() -> None:
    frame = _frame(900)
    bars = np.arange(500, 700)
    eligible = pd.DataFrame(
        {
            "bar_i": bars,
            "signal_time": frame.open_time.iloc[bars].astype(str).to_numpy(),
            "gap_run_id": np.where(bars < 600, 1, 2),
            "strict_oos": False,
            "atr_pct": np.linspace(0.001, 0.004, len(bars)),
            "train_pixel_overlap_bars": 0,
        }
    )
    eligible = assign_atr_buckets(eligible)
    base = eligible[(eligible.gap_run_id == 1) & (eligible.atr_bucket == 0)].iloc[10]
    signal = {**base.to_dict(), **short_hold_outcome(frame, int(base.bar_i)), "scope": "gap_replay"}
    controls, enriched = matched_controls(frame, eligible, pd.DataFrame([signal]))
    assert len(controls) == 3
    assert controls.control_gap_run_id.eq(int(base.gap_run_id)).all()
    assert controls.control_atr_bucket.eq(int(base.atr_bucket)).all()
    assert controls.match_tier.eq("same_run_atr_quintile").all()
    assert enriched.paired_excess.notna().all()


def test_scan_fingerprint_changes_when_model_changes(tmp_path) -> None:
    data = tmp_path / "data.csv"
    manifest = tmp_path / "manifest.csv"
    weights = tmp_path / "weights.pt"
    data.write_bytes(b"data")
    manifest.write_bytes(b"manifest")
    weights.write_bytes(b"model-a")
    eligible = pd.DataFrame(
        {
            "bar_i": [500],
            "signal_time": ["2026-03-02 01:00:00+00:00"],
            "gap_run_id": [1],
            "strict_oos": [True],
        }
    )
    first = scan_fingerprint(
        data_path=data,
        manifest_path=manifest,
        weights_path=weights,
        eligible=eligible,
        device="mps",
        batch_size=32,
    )
    weights.write_bytes(b"model-b")
    second = scan_fingerprint(
        data_path=data,
        manifest_path=manifest,
        weights_path=weights,
        eligible=eligible,
        device="mps",
        batch_size=32,
    )
    assert first["signature"] != second["signature"]
