"""Tests for the standalone causal V12F Python conversion."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.run_pine_eth_15m_v12f import (
    BAR,
    MAX_SAFE_END,
    PINE_SHA256,
    build_signal_ledger,
    file_sha256,
    run,
    signal_parameters,
    validate_frozen_pine,
    validate_period,
)


def test_python_contract_is_bound_to_the_frozen_v12f_pine() -> None:
    validate_frozen_pine()
    from scripts.run_pine_eth_15m_v12f import PINE_PATH

    assert file_sha256(PINE_PATH) == PINE_SHA256
    params = signal_parameters()
    assert (params.fast_len, params.slow_len, params.regime_len) == (10, 60, 100)
    assert (params.osc_basis_len, params.osc_percentile_len) == (40, 200)
    assert (params.osc_change_lag, params.osc_hma_len) == (10, 10)
    assert params.osc_threshold == 0.1
    assert params.atr_mult == 4.0
    assert params.max_sl_percent == 3.0


def test_signal_ledger_uses_a_bar_interval_not_exact_timestamp_matching() -> None:
    times = pd.date_range("2023-01-01", periods=3, freq="15min", tz="UTC")
    featured = pd.DataFrame(
        {
            "open_time": times,
            "entry_allowed": [True, True, True],
            "v12f_long": [False, True, False],
            "v12f_short": [False, False, False],
            "close": [100.0, 101.0, 102.0],
            "atr": [1.0, 1.1, 1.2],
            "atr_percent": [1.0, 1.1, 1.2],
            "osc": [0.0, 0.2, 0.1],
            "slow_slope_12": [0.0, 0.01, 0.01],
            "six_ma_cross_up_count_8": [0, 2, 2],
            "six_ma_cross_down_count_8": [0, 1, 1],
        }
    )
    ledger = build_signal_ledger(
        featured,
        symbol="ETH_USDT_SWAP",
        start=times[0],
        end=times[-1] + 2 * BAR,
    )

    assert len(ledger) == 1
    row = ledger.iloc[0]
    assert row["direction"] == "long"
    assert row["interval_start"] == times[1]
    assert row["interval_end"] == times[1] + BAR
    assert row["available_at"] == times[1] + BAR
    assert row["next_open_time"] == times[2]
    assert row["ma6_w8_cross_imbalance"] == 1.0


def test_conversion_cli_refuses_to_reopen_the_consumed_holdout() -> None:
    validate_period(pd.Timestamp("2025-01-01", tz="UTC"), MAX_SAFE_END)
    with pytest.raises(RuntimeError, match="pre-holdout only"):
        validate_period(
            pd.Timestamp("2025-01-01", tz="UTC"),
            MAX_SAFE_END + BAR,
        )


def test_run_writes_inspectable_outputs_from_synthetic_15m_data(tmp_path: Path) -> None:
    rows = 640
    times = pd.date_range("2023-01-01", periods=rows, freq="15min", tz="UTC")
    phase = np.linspace(0.0, 14.0 * np.pi, rows)
    close = 100.0 + 0.015 * np.arange(rows) + 4.0 * np.sin(phase)
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": close - 0.05,
            "high": close + 0.35,
            "low": close - 0.35,
            "close": close,
            "volume": 1000.0 + 50.0 * np.cos(phase),
        }
    )
    input_path = tmp_path / "eth_15m.csv"
    output_dir = tmp_path / "out"
    frame.to_csv(input_path, index=False)

    summary = run(
        input_path=input_path,
        output_dir=output_dir,
        symbol="ETH_USDT_SWAP",
        start=times[240],
        end=times[-1] + BAR,
    )

    assert {path.name for path in output_dir.iterdir()} == {
        "signals.csv",
        "trades.csv",
        "equity.csv",
        "summary.json",
    }
    written = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert written == summary
    assert written["timeframe"] == "15m"
    assert written["data"]["holdout_rows_read"] == 0
    assert written["fusion_contract"]["exact_same_bar_required"] is False
    assert written["fusion_contract"]["model_detection_used_in_this_run"] is False
    assert written["safety"]["places_orders"] is False
    assert written["pine_sha256"] == PINE_SHA256
