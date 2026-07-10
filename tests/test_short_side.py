from __future__ import annotations

import pandas as pd
import pytest

from src.judgment.candidates import short_mask, strict_mask
from src.judgment.labeling import label_short_candidate


def test_short_label_hits_lower_target_first() -> None:
    frame = pd.DataFrame(
        {
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 94.0, 99.0],
            "close": [100.0, 100.0, 100.0, 98.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )

    outcome = label_short_candidate(frame, 0, tp_mult=5.0, sl_mult=2.0, horizon=3)

    assert outcome is not None
    assert outcome.label == 1
    assert outcome.outcome == "tp"
    assert outcome.exit_offset == 2
    assert outcome.realized_ret == pytest.approx(100.0 / 95.0 - 1)


def test_short_label_stop_and_ambiguous_are_conservative() -> None:
    stop_frame = pd.DataFrame(
        {
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 103.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )
    ambiguous_frame = stop_frame.copy()
    ambiguous_frame.loc[1, "low"] = 94.0

    stop = label_short_candidate(stop_frame, 0, tp_mult=5.0, sl_mult=2.0, horizon=3)
    ambiguous = label_short_candidate(ambiguous_frame, 0, tp_mult=5.0, sl_mult=2.0, horizon=3)

    assert stop is not None
    assert stop.label == 0
    assert stop.outcome == "sl"
    assert stop.realized_ret == pytest.approx(100.0 / 102.0 - 1)
    assert ambiguous is not None
    assert ambiguous.label == 0
    assert ambiguous.outcome == "sl_ambiguous"
    assert ambiguous.realized_ret == pytest.approx(100.0 / 102.0 - 1)


def test_short_mask_uses_down_order_and_down_extension_not_long_order() -> None:
    row = pd.DataFrame(
        {
            "fast_spread": [0.001],
            "full_spread": [0.002],
            "fast_slow_gap": [0.001],
            "full_ratio_min48": [1.0],
            "pre_range48": [0.01],
            "pre_range168": [0.02],
            "drawdown24": [0.02],
            "runup24": [0.001],
            "ext_up": [0.05],
            "ext_down": [0.001],
            "order_score": [0],
            "down_order_score": [4],
            "slow_slope_abs": [0.0],
            "zero_volume96": [0.0],
            "volume_ratio": [1.0],
            "dense_run_len_expanded": [5],
        }
    )

    assert not strict_mask(row, mode="expanded").iloc[0]
    assert short_mask(row, mode="expanded").iloc[0]
