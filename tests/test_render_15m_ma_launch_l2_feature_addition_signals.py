from __future__ import annotations

import pandas as pd

from scripts.render_15m_ma_launch_l2_feature_addition_signals import (
    chart_filename,
    select_signal_rows,
    summarize_outcomes,
)


def test_select_signal_rows_keeps_only_independent_q90_rows() -> None:
    dataset = pd.DataFrame(
        [
            {
                "episode_id": "a",
                "available_at": "2026-04-01T00:15:00Z",
                "split": "final_validation",
                "dependency_representative": True,
                "outcome": "tp",
                "label": 1,
                "realized_ret": 0.03,
                "net_ret": 0.028,
            },
            {
                "episode_id": "b",
                "available_at": "2026-04-01T01:15:00Z",
                "split": "final_validation",
                "dependency_representative": True,
                "outcome": "sl",
                "label": 0,
                "realized_ret": -0.01,
                "net_ret": -0.012,
            },
            {
                "episode_id": "c",
                "available_at": "2026-03-01T00:15:00Z",
                "split": "tune",
                "dependency_representative": True,
                "outcome": "tp",
                "label": 1,
                "realized_ret": 0.02,
                "net_ret": 0.018,
            },
        ]
    )
    scored = pd.DataFrame(
        [
            {
                "episode_id": "a",
                "dependency_representative": True,
                "selected_keep": True,
                "selected_arm": "full_110",
                "selected_score": 0.2,
                "selected_percentile": 0.95,
                "selected_threshold": 0.1,
            },
            {
                "episode_id": "b",
                "dependency_representative": True,
                "selected_keep": False,
                "selected_arm": "full_110",
                "selected_score": 0.0,
                "selected_percentile": 0.5,
                "selected_threshold": 0.1,
            },
            {
                "episode_id": "c",
                "dependency_representative": False,
                "selected_keep": True,
                "selected_arm": "full_110",
                "selected_score": 0.3,
                "selected_percentile": 0.99,
                "selected_threshold": 0.1,
            },
        ]
    )
    selected = select_signal_rows(dataset, scored)
    assert selected["episode_id"].tolist() == ["a"]
    assert selected["net_profitable"].tolist() == [True]


def test_summary_separates_tp_label_from_after_cost_profit() -> None:
    rows = pd.DataFrame(
        {
            "outcome": ["tp", "sl", "timeout", "timeout"],
            "label": [1, 0, 0, 0],
            "barrier_positive": [True, False, False, False],
            "net_profitable": [True, False, True, False],
            "side": ["long", "long", "short", "short"],
            "realized_ret": [0.03, -0.01, 0.01, -0.01],
            "net_ret": [0.028, -0.012, 0.008, -0.012],
            "available_at": [
                "2026-04-01T00:15:00Z",
                "2026-04-01T01:15:00Z",
                "2026-04-01T02:15:00Z",
                "2026-04-01T03:15:00Z",
            ],
        }
    )
    summary = summarize_outcomes(rows)
    assert summary["barrier_positive_labels"] == 1
    assert summary["net_profitable"] == 2
    assert summary["positive_timeouts"] == 1
    assert summary["negative_timeouts"] == 1


def test_chart_filename_exposes_net_state_and_outcome() -> None:
    row = {
        "net_profitable": True,
        "outcome": "timeout",
        "symbol": "ARB_USDT_SWAP",
        "side": "short",
        "available_at": "2026-04-02T01:45:00Z",
    }
    assert chart_filename(row, 3) == "03_WIN_TIMEOUT_ARB_SHORT_20260402T0145Z.png"
