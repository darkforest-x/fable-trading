"""The evaluation gates must refuse the results this project has been fooled by.

Each test reconstructs a specific historical failure in miniature and checks
that the gate now catches it. A gate suite that only proves the arithmetic
would pass on all three of them.
"""
from __future__ import annotations

import numpy as np
import pytest

from yoyo.evaluation.economic_gates import (
    ACCEPTANCE_ROUND_TRIP_COST,
    evaluate_economic_gates,
)
from yoyo.evaluation.permutation import (
    DEFAULT_ALPHA,
    permutation_test,
    top_decile_mean,
)
from yoyo.evaluation.walk_forward import (
    Fold,
    SplitLeakageError,
    assert_no_split_leakage,
    assign_splits,
    build_anchored_folds,
)


# -- walk-forward ----------------------------------------------------------

def test_anchored_folds_expand_and_tile_the_tail():
    folds = build_anchored_folds(1000, 4, 0.6)
    assert [f.test_lo for f in folds] == [600, 700, 800, 900]
    assert [f.test_hi for f in folds] == [700, 800, 900, 1000]
    # anchored: each fold trains on everything before its own test slice
    assert [f.train_end for f in folds] == [600, 700, 800, 900]


def test_a_degenerate_fold_request_is_refused():
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        build_anchored_folds(1000, 4, 1.0)
    with pytest.raises(ValueError, match="cannot be split"):
        build_anchored_folds(3, 4, 0.5)


def test_train_events_stop_a_full_horizon_before_the_test_slice():
    fold = Fold(index=0, train_end=600, test_lo=600, test_hi=700)
    positions = [100, 500, 595, 605, 610, 640]
    splits = assign_splits(
        positions, fold, horizon_bars=24, gap_bars=10, inner_val_start=400
    )
    assigned = dict(zip(positions, splits))
    assert assigned[100] == "train"
    # 595 + 24 would reach bar 619, inside the test slice -> dropped, not val
    assert assigned[595] == "dropped"
    # the embargo covers [600, 610): 605 is inside it, 610 is the first bar past it
    assert assigned[605] == "dropped"
    assert assigned[610] == "test"
    assert assigned[640] == "test"


def test_the_leakage_check_catches_a_split_that_reaches_into_test():
    fold = Fold(index=0, train_end=600, test_lo=600, test_hi=700)
    positions = [590, 640]
    lying_splits = ["val", "test"]  # 590 + 24 = 614, past test_lo
    with pytest.raises(SplitLeakageError, match="label window"):
        assert_no_split_leakage(
            positions, lying_splits, fold, horizon_bars=24, gap_bars=10
        )


def test_the_leakage_check_catches_a_test_event_inside_the_embargo():
    fold = Fold(index=0, train_end=600, test_lo=600, test_hi=700)
    with pytest.raises(SplitLeakageError, match="embargo"):
        assert_no_split_leakage([602], ["test"], fold, horizon_bars=24, gap_bars=10)


def test_a_correct_split_passes_its_own_audit():
    fold = Fold(index=0, train_end=600, test_lo=600, test_hi=700)
    positions = [100, 450, 640, 690]
    splits = assign_splits(positions, fold, horizon_bars=24, gap_bars=10, inner_val_start=400)
    assert_no_split_leakage(positions, splits, fold, horizon_bars=24, gap_bars=10)


# -- permutation -----------------------------------------------------------

def test_a_real_ranking_is_detected():
    rng = np.random.default_rng(1)
    scores = rng.normal(size=300)
    outcomes = scores * 0.01 + rng.normal(scale=0.002, size=300)
    result = permutation_test(scores, outcomes, n_permutations=2000, seed=1)
    assert result.passes(DEFAULT_ALPHA)


def test_pure_noise_does_not_clear_the_project_standard():
    rng = np.random.default_rng(2)
    scores = rng.normal(size=300)
    outcomes = rng.normal(size=300)
    result = permutation_test(scores, outcomes, n_permutations=2000, seed=2)
    assert not result.passes(DEFAULT_ALPHA)


def test_the_p_value_is_never_exactly_zero():
    """A reported 0.0000 reads as certainty; it means 'below 1/(n+1)'."""
    scores = np.arange(200, dtype=float)
    outcomes = np.arange(200, dtype=float)
    result = permutation_test(scores, outcomes, n_permutations=999, seed=3)
    assert result.p_value > 0.0
    assert result.p_value == pytest.approx(1 / 1000)


# -- the three economic gates ---------------------------------------------

def _ranked(n=400, edge=0.004, seed=5):
    rng = np.random.default_rng(seed)
    scores = rng.normal(size=n)
    gross = np.where(scores > np.quantile(scores, 0.9), edge, 0.0)
    gross = gross + rng.normal(scale=0.0002, size=n)
    return scores, gross


def test_a_genuine_edge_clears_all_three_gates():
    scores, gross = _ranked(edge=0.006)
    control = np.full(400, 0.0)
    verdict = evaluate_economic_gates(scores, gross, control, n_permutations=2000)
    assert verdict.accepted, verdict.summary()


def test_a_gross_edge_smaller_than_the_fee_is_rejected():
    """v1's lesson: AUC 0.59 and still losing money."""
    scores, gross = _ranked(edge=0.001)  # 10bp gross against a 20bp round trip
    verdict = evaluate_economic_gates(scores, gross, np.full(400, 0.0), n_permutations=2000)
    assert not verdict.accepted
    assert "net_return_after_cost" in verdict.failed_gates


def test_a_pool_standing_on_beta_is_rejected_even_though_it_earns():
    """The 100x6m pool: +16.9bp of which +7.2bp was simply being short.

    Every event earns the same amount, so the ranking is meaningless, and the
    matched control earns it too. The money is real and none of it is the model.
    """
    rng = np.random.default_rng(7)
    scores = rng.normal(size=400)
    beta = 0.008
    gross = np.full(400, beta) + rng.normal(scale=0.0001, size=400)
    control = np.full(2000, beta)
    verdict = evaluate_economic_gates(scores, gross, control, n_permutations=2000)
    assert not verdict.accepted
    assert "beats_matched_control" in verdict.failed_gates
    assert "permutation" in verdict.failed_gates


def test_omitting_the_control_is_a_refusal_not_a_default():
    scores, gross = _ranked()
    with pytest.raises(ValueError, match="cannot be distinguished from the period"):
        evaluate_economic_gates(scores, gross, [], n_permutations=100)


def test_the_acceptance_cost_is_the_documented_owner_value():
    assert ACCEPTANCE_ROUND_TRIP_COST == 0.002


def test_every_gate_reports_its_number_whether_it_passed_or_not():
    scores, gross = _ranked(edge=0.001)
    verdict = evaluate_economic_gates(scores, gross, np.full(400, 0.0), n_permutations=500)
    assert len(verdict.gates) == 3
    assert all(np.isfinite(gate.value) for gate in verdict.gates)
    assert "REJECTED" in verdict.summary()
