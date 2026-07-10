from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.judgment.forward_threshold_shadow import (
    ThresholdShadowPathError,
    summarize_threshold_scores,
    validate_shadow_output,
)
from src.judgment.forward_types import FORWARD_LOG_H1_SCALED_PATH, FORWARD_LOG_PATH


def test_summarize_threshold_scores_compares_the_same_forward_candidates() -> None:
    result = summarize_threshold_scores(
        np.array([np.nan, 0.20, 0.30, 0.40, 0.50]),
        q90_threshold=0.45,
        q80_threshold=0.30,
    )

    assert result.candidates_after_start == 5
    assert result.finite_scores == 4
    assert result.q90_signals == 1
    assert result.q80_signals == 3
    assert result.q80_incremental_signals == 2
    assert result.q90_pass_rate == pytest.approx(0.25)
    assert result.q80_pass_rate == pytest.approx(0.75)


def test_summarize_threshold_scores_rejects_reversed_thresholds() -> None:
    with pytest.raises(ValueError, match="q80"):
        summarize_threshold_scores(
            np.array([0.2, 0.3]),
            q90_threshold=0.2,
            q80_threshold=0.3,
        )


@pytest.mark.parametrize("path", [FORWARD_LOG_PATH, FORWARD_LOG_H1_SCALED_PATH])
def test_validate_shadow_output_rejects_existing_books(path: Path) -> None:
    with pytest.raises(ThresholdShadowPathError, match="shadow"):
        validate_shadow_output(path)


def test_validate_shadow_output_accepts_isolated_q80_book(tmp_path: Path) -> None:
    path = tmp_path / "forward_log_ma206_q80_shadow.csv"

    assert validate_shadow_output(path) == path.resolve()
