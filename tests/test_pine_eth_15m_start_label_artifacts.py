"""Recompute the saved Pine start-label and ATR-gate audit invariants."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.prepare_pine_eth_15m_judgment_research import load_development_features
from scripts.research_pine_eth_15m import Period, _atr_month_buckets


PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / "experiments/active/exp-pine-eth-15m-v1/results"


def test_saved_start_labels_recompute_exact_counts_and_boundaries() -> None:
    rows = pd.read_csv(RESULTS / "judgment_start_label_rows.csv")
    audit = json.loads((RESULTS / "judgment_start_label_audit.json").read_text())
    assert len(rows) == 335
    assert rows["candidate_id"].is_unique
    assert rows["status"].eq("resolved").all()
    assert int(rows["start_label"].sum()) == 184
    assert int(rows["start_label"].eq(0).sum()) == 151
    assert rows["outcome"].value_counts().to_dict() == {
        "target": 184,
        "initial_stop": 148,
        "opposite_signal_exit": 3,
    }
    assert pd.to_datetime(rows["signal_time"], utc=True).lt(
        pd.Timestamp("2025-01-01T00:00:00Z")
    ).all()
    assert pd.to_datetime(rows["label_available_at"], utc=True).gt(
        pd.to_datetime(rows["features_available_at"], utc=True)
    ).all()
    assert audit["consumed_final_rows_read"] == 0
    assert audit["holdout_rows_read"] == 0


def test_saved_controls_recompute_exact_causal_strata_and_no_reuse() -> None:
    controls = pd.read_csv(RESULTS / "judgment_start_label_matched_controls.csv")
    assert len(controls) == 1_005
    assert controls["control_signal_i"].is_unique
    assert controls.groupby("candidate_id")["control_rank"].count().eq(3).all()
    assert controls["control_status"].eq("resolved").all()

    featured, quality = load_development_features()
    assert quality["consumed_final_rows_read"] == 0
    assert quality["holdout_rows_read"] == 0
    times = pd.to_datetime(featured["open_time"], utc=True)
    period = Period(
        "development",
        pd.Timestamp("2023-01-01T00:00:00Z"),
        pd.Timestamp("2025-01-01T00:00:00Z"),
    )
    buckets = _atr_month_buckets(featured, period, buckets=5)
    indices = controls["control_signal_i"].astype(int).to_numpy()
    actual_times = times.iloc[indices].reset_index(drop=True)
    assert actual_times.dt.strftime("%Y-%m").eq(controls["match_month"]).all()
    actual_blocks = actual_times.dt.tz_convert("Asia/Hong_Kong").dt.hour // 6
    assert actual_blocks.to_numpy(dtype=int).tolist() == controls[
        "match_hk_6h_block"
    ].astype(int).tolist()
    assert buckets[indices].tolist() == controls["match_causal_atr_quintile"].astype(int).tolist()
    assert (
        indices + controls["copied_horizon_bars"].astype(int).to_numpy() + 1
        < len(featured)
    ).all()


def test_saved_64_seed_summary_recomputes_and_supports_gate_rejection() -> None:
    sensitivity = pd.read_csv(RESULTS / "judgment_atr_expansion_gate_control_sensitivity.csv")
    payload = json.loads((RESULTS / "judgment_atr_expansion_gate.json").read_text())
    assert len(sensitivity) == 4 * 64
    results = {(row["arm"], row["period"]): row for row in payload["results"]}
    for (arm, period), group in sensitivity.groupby(["gate_arm", "period"]):
        values = group["candidate_minus_control_bp_per_trade"]
        saved = results[(arm, str(period))]["control_seed_sensitivity"]
        assert len(group) == 64
        assert saved["excess_bp_median"] == pytest.approx(values.median())
        assert saved["excess_bp_p05"] == pytest.approx(values.quantile(0.05))
        assert saved["excess_bp_p95"] == pytest.approx(values.quantile(0.95))
        assert saved["positive_excess_seed_fraction"] == pytest.approx(values.gt(0).mean())

    assert payload["decision"].startswith("reject")
    assert payload["gate_minus_allow_all"]["2023"]["return_delta_percentage_points"] < 0
    assert payload["gate_minus_allow_all"]["2024"]["return_delta_percentage_points"] < 0
    assert payload["gate_minus_allow_all"]["2024"]["drawdown_delta_percentage_points"] > 0
    assert payload["consumed_final_rows_read"] == 0
    assert payload["holdout_rows_read"] == 0
