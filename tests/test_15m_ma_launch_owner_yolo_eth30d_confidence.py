from __future__ import annotations

import math

from scripts.analyze_15m_ma_launch_owner_yolo_eth30d_confidence import (
    build_bin_table,
    build_threshold_table,
    enrich_episodes,
    permutation_spearman,
    read_source_tables,
)


def _evidence():
    episodes, candidates, _ = read_source_tables()
    return enrich_episodes(episodes, candidates), candidates


def test_frozen_confidence_source_contract():
    enriched, candidates = _evidence()
    assert len(enriched) == 41
    assert len(candidates) == 1057
    assert enriched["episode_id"].nunique() == 41
    assert math.isclose(enriched["representative_confidence"].median(), 0.429350197315216)
    assert math.isclose(enriched["max_confidence"].median(), 0.7896889448165894)


def test_confidence_bins_keep_both_time_semantics_separate():
    enriched, _ = _evidence()
    bins = build_bin_table(enriched)
    earliest = bins[bins["score_type"] == "earliest_visible"]["episodes"].tolist()
    eventual = bins[bins["score_type"] == "episode_max"]["episodes"].tolist()
    assert earliest == [12, 11, 10, 4, 4]
    assert eventual == [7, 3, 9, 4, 18]


def test_threshold_sensitivity_reclusters_raw_candidates():
    enriched, candidates = _evidence()
    table = build_threshold_table(enriched, candidates)
    indexed = table.set_index("threshold")
    assert int(indexed.loc[0.25, "episodes"]) == 41
    assert int(indexed.loc[0.50, "episodes"]) == 31
    assert int(indexed.loc[0.75, "episodes"]) == 22
    assert int(indexed.loc[0.90, "episodes"]) == 18
    assert int(indexed.loc[0.95, "episodes"]) == 8
    assert int(indexed.loc[0.90, "max_extra_delay_bars"]) == 12


def test_repeat_association_control_distinguishes_early_and_max_scores():
    enriched, _ = _evidence()
    early = permutation_spearman(
        enriched["representative_confidence"],
        enriched["episode_candidate_count"],
        permutations=999,
    )
    maximum = permutation_spearman(
        enriched["max_confidence"],
        enriched["episode_candidate_count"],
        permutations=999,
    )
    assert 0.25 < float(early["spearman_rho"]) < 0.30
    assert float(early["two_sided_p"]) > 0.05
    assert float(maximum["spearman_rho"]) > 0.85
    assert float(maximum["two_sided_p"]) <= 0.002
