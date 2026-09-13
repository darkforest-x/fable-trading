"""Unknown coverage must survive an explicit-only HTF exclusion audit."""
import pandas as pd

def test_postprocess_keeps_htf_unknown_in_explicit_deletion_complement() -> None:
    from yoyo.evaluation.spike_v8_entry_evidence_postprocess import build_tables

    events = pd.DataFrame(
        {
            "period": ["validation"] * 4,
            "scoring_closed": [True] * 4,
            "net_r": [-1.0, 2.0, 10.0, -2.0],
            "bb_episode_directional_order9_run3": [True, False, True, False],
            "bb_ma_tight_overlap_run3": [True, False, True, False],
            "htf_opposed_completed": [True, False, pd.NA, False],
        }
    )
    _, deletion = build_tables(events)
    all_not_explicit = deletion.loc[deletion.cohort.eq("all_not_explicitly_opposed")].iloc[0]
    unknown = deletion.loc[deletion.cohort.eq("unknown_htf")].iloc[0]
    assert (all_not_explicit.n, all_not_explicit.realized_10r) == (3, 1)
    assert unknown.n == 1
    assert all_not_explicit.unknown_scoring_closed == 1
