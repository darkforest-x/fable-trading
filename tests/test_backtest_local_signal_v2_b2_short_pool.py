from __future__ import annotations

import pandas as pd
import pytest

from scripts.audit_local_signal_v2_b2_density import evaluation_density
from scripts.backtest_local_signal_v2_b2_short_pool import (
    build_matched_controls,
    causal_gap_dedup,
    exact_week_signflip_p,
    remove_validation_overlap,
    summarize_returns,
)


def _row(candidate: str, symbol: str, signal_i: int, fired: bool = True) -> dict:
    return {
        "candidate_id": candidate,
        "symbol": symbol,
        "mapped_signal_i": signal_i,
        "signal_time": pd.Timestamp("2026-03-20T06:00:00Z")
        + pd.Timedelta(minutes=15 * signal_i),
        "b2_fire_edge3": fired,
        "gross_ret": 0.01,
        "net_ret_swap_taker": 0.009,
        "label_tp_before_sl": 1,
        "exit_reason": "tp",
    }


def test_overlap_exclusion_is_same_symbol_and_inclusive():
    rows = pd.DataFrame([_row("a", "A", 100), _row("b", "A", 173), _row("c", "B", 100)])
    kept, n = remove_validation_overlap(rows, {"A": [172]}, tolerance=72)
    assert n == 2
    assert kept["candidate_id"].tolist() == ["c"]


def test_causal_gap_dedup_keeps_first_after_gap():
    rows = pd.DataFrame(
        [_row("a", "A", 100), _row("b", "A", 110), _row("c", "A", 118), _row("d", "B", 105)]
    )
    kept = causal_gap_dedup(rows, fire_col="b2_fire_edge3", gap_bars=18)
    assert set(kept["candidate_id"]) == {"a", "c", "d"}


def test_return_summary_applies_cost_once():
    rows = pd.DataFrame([_row("a", "A", 100), {**_row("b", "B", 101), "gross_ret": -0.004, "net_ret_swap_taker": -0.005, "label_tp_before_sl": 0, "exit_reason": "sl"}])
    out = summarize_returns(rows)
    assert out["mean_gross_bp"] == pytest.approx(30.0)
    assert out["mean_net_taker_10bp"] == pytest.approx(20.0)
    assert out["mean_net_conservative_20bp"] == pytest.approx(10.0)
    assert out["n"] == 2


def test_exact_week_signflip_uses_block_level_signs():
    matched = pd.DataFrame(
        {
            "week": ["2026-W12", "2026-W12", "2026-W13", "2026-W13"],
            "excess": [0.01, 0.01, 0.02, 0.02],
        }
    )
    p, n_weeks = exact_week_signflip_p(matched)
    assert n_weeks == 2
    assert p == 0.5


def test_matched_control_records_month_fallback():
    base = pd.Timestamp("2026-03-20T06:00:00Z")
    pool = pd.DataFrame(
        [
            {
                **_row(f"c{i}", "A", 100 + i),
                "event_group_id": f"g{i}",
                "atr_pct": atr,
                "signal_time": base + pd.Timedelta(hours=i),
            }
            for i, atr in enumerate((0.001, 0.002, 0.010, 0.011, 0.012))
        ]
    )
    selected = pool.iloc[[0]].copy()
    matched, meta = build_matched_controls(selected, pool, n_per=2)
    assert len(matched) == 1
    assert bool(matched.iloc[0]["fallback_same_symbol_month"])
    assert meta["n_month_fallback_attempts"] == 1


def test_density_audit_counts_endpoints_not_boxes():
    rows = [
        {"eval_id": "p1", "sample_type": "positive"},
        {"eval_id": "p2", "sample_type": "positive"},
        {"eval_id": "n1", "sample_type": "easy_negative"},
        {"eval_id": "n2", "sample_type": "easy_negative"},
    ]
    predictions = {
        "p1": [{"confidence": 0.4}, {"confidence": 0.39}],
        "p2": [],
        "n1": [{"confidence": 0.5}, {"confidence": 0.45}],
        "n2": [{"confidence": 0.2}],
    }
    out = evaluation_density({"predictions": predictions}, rows, 0.35)
    assert out["positive_endpoints_with_any_box"] == 1
    assert out["easy_negative_endpoints_with_any_box"] == 1
    assert out["easy_negative_endpoint_fire_rate"] == 0.5
    assert out["all_endpoint_fire_rate"] == 0.5
