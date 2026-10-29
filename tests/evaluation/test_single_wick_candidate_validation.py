"""Regression checks for outcome-independent filters and control period boundaries."""
import pandas as pd

from yoyo.evaluation.single_wick_candidate_validation import select_ids, period_rows


def test_filter_keeps_unresolved_signals_and_rejects_outcome_shortcut():
    frame = pd.DataFrame([
        dict(event_id='winner', population='signal', side=-1, risk_bp=60., closed=True, target_hit=True),
        dict(event_id='unresolved', population='signal', side=-1, risk_bp=70., closed=False, target_hit=False),
        dict(event_id='long', population='signal', side=1, risk_bp=70., closed=True, target_hit=True),
        dict(event_id='control', population='random', side=-1, risk_bp=80., closed=True, target_hit=True),
    ])
    assert select_ids(frame, dict(feature='risk_bp', cut=63.621485488253036)) == {'unresolved'}


def test_each_control_must_fit_same_evaluation_period():
    frame = pd.DataFrame([
        dict(population='signal', entry_time='2025-01-01T00:00Z', exit_time='2025-01-02T00:00Z', closed=True),
        dict(population='random', entry_time='2024-12-31T00:00Z', exit_time='2025-01-02T00:00Z', closed=True),
        dict(population='random', entry_time='2025-01-01T00:00Z', exit_time='2026-01-01T00:00Z', closed=True),
        dict(population='signal', entry_time='2025-12-31T00:00Z', exit_time='2026-01-01T00:00Z', closed=True),
        dict(population='signal', entry_time='2025-06-01T00:00Z', exit_time='2025-06-02T00:00Z', closed=False),
    ])
    result, counts = period_rows(frame, '2025-01-01', '2026-01-01')
    assert result.index.tolist() == [0, 4]
    assert counts == dict(entry_cohort_n=3, entry_cohort_censored_n=1, cross_boundary_n=1)
