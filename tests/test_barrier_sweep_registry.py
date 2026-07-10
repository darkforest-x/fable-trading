from __future__ import annotations

import pandas as pd
import pytest

from src.judgment.barrier_sweep import EXIT_PLUGINS, label_with_config
from src.judgment.labeling import label_candidate


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [99.0, 100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 106.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 98.0, 98.0],
            "close": [100.0, 101.0, 99.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01, 0.01],
            "ema20": [100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )


def test_exit_registry_contains_research_agenda_plugins() -> None:
    assert set(EXIT_PLUGINS) == {"fixed", "trailing", "scaled", "breakeven", "ma-exit"}


def test_fixed_plugin_matches_legacy_label_candidate() -> None:
    frame = _frame()
    cfg = {"exit": "fixed", "tp": 5.0, "sl": 2.0, "horizon": 3}

    assert label_with_config(frame, 0, cfg) == label_candidate(frame, 0, tp_mult=5.0, sl_mult=2.0, horizon=3)


def test_ma_exit_plugin_closes_on_close_below_ma() -> None:
    outcome = label_with_config(_frame(), 0, {"exit": "ma-exit", "horizon": 3})

    assert outcome is not None
    assert outcome.outcome == "ma_exit"
    assert outcome.exit_offset == 2
    assert outcome.realized_ret == pytest.approx(-0.01)


def test_unknown_exit_plugin_fails_fast() -> None:
    with pytest.raises(ValueError, match="unknown exit plugin"):
        label_with_config(_frame(), 0, {"exit": "unknown", "horizon": 3})
