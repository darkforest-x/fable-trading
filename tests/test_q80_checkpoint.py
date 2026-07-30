from __future__ import annotations

import pandas as pd
import pytest

from src.judgment.q80_checkpoint import Q80CheckpointError, build_q80_checkpoint


def _latest(latest_bar: str) -> dict:
    return {
        "funnel": {
            "start_time": "2026-07-10 10:30:00+00:00",
            "latest_bar_time": latest_bar,
            "q90_threshold": 0.40,
            "q80_threshold": 0.30,
            "score_summary": {
                "candidates_after_start": 10,
                "q90_actionable_signals": 2,
                "q80_actionable_signals": 4,
            },
        },
        "q80_shadow": {"total_rows": 3},
    }


def _ledger() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source": ["okx", "okx", "okx"],
            "symbol": ["A_USDT_SWAP", "B_USDT_SWAP", "C_USDT_SWAP"],
            "signal_time": ["2026-07-10 11:00:00+00:00"] * 3,
            "status": ["closed", "closed", "open"],
            "score": [0.45, 0.35, 0.31],
            "outcome": ["tp", "sl", None],
            "realized_ret": [0.01, -0.004, None],
        }
    )


def test_checkpoint_waits_for_24_market_hours() -> None:
    payload = build_q80_checkpoint(_latest("2026-07-11 10:15:00+00:00"), _ledger())

    assert payload["status"] == "not_ready"
    assert payload["elapsed_hours"] == pytest.approx(23.75)


def test_checkpoint_seals_fixed_cost_q90_and_q80_only_groups() -> None:
    payload = build_q80_checkpoint(_latest("2026-07-11 10:30:00+00:00"), _ledger())

    assert payload["status"] == "ready"
    assert payload["ledger"] == {
        "total_rows": 3,
        "closed_rows": 2,
        "open_rows": 1,
        "finite_scores": 3,
        "duplicate_rows": 0,
    }
    assert payload["closed_economics"]["q90_score_range"]["net_mean_per_trade"] == pytest.approx(
        0.008
    )
    assert payload["closed_economics"]["q80_only"]["net_mean_per_trade"] == pytest.approx(-0.006)


def test_checkpoint_rejects_duplicate_signal_keys() -> None:
    ledger = _ledger()
    duplicate = pd.concat([ledger, ledger.iloc[[0]]], ignore_index=True)

    with pytest.raises(Q80CheckpointError, match="duplicate"):
        build_q80_checkpoint(_latest("2026-07-11 10:30:00+00:00"), duplicate)
