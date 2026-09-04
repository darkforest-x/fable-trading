from __future__ import annotations

import pandas as pd

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import filter_candidates
from scripts.research_btcusdtp_k1k2_15m_gap_min_confirmation import params_for


def test_gap_filter_precedes_same_k2_ranking() -> None:
    universe = pd.DataFrame(
        {
            "k2_i": [100, 100, 110],
            "direction": [1, 1, -1],
            "k1_i": [98, 95, 104],
            "gap_bars": [2, 5, 6],
            "secondary_score": [0.90, 0.70, 0.80],
        }
    )
    baseline = filter_candidates(
        universe,
        {"gap_min_bars": 2, "gap_max_bars": 8, "score_floor": 0.4},
    )
    candidate = filter_candidates(
        universe,
        {"gap_min_bars": 5, "gap_max_bars": 8, "score_floor": 0.4},
    )
    assert baseline.loc[baseline["k2_i"].eq(100), "gap_bars"].item() == 2
    assert candidate.loc[candidate["k2_i"].eq(100), "gap_bars"].item() == 5
    assert candidate["gap_bars"].ge(5).all()


def test_registered_candidate_changes_only_gap_minimum() -> None:
    config = {
        "signal_frozen": {
            "15m": {"ma_period": 120, "score_floor": 0.4}
        },
        "factor": {"gap_max_bars_frozen": 8},
    }
    baseline = params_for(config, 2)
    candidate = params_for(config, 5)
    changed = {key for key in baseline if baseline[key] != candidate[key]}
    assert changed == {"gap_min_bars"}
