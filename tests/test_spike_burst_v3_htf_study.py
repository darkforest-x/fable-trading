"""Synthetic contracts for E label denominators, parent economics and Holm5."""
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_v3_htf_study as study


def fixture_tables():
    times = pd.to_datetime(["2026-08-01T12:00Z", "2026-08-02T12:00Z"])
    labels = pd.DataFrame(dict(instrument=["x", "x"], event_i=[100, 124],
        decision_time=times, label=["positive", "positive"], large_peak=[True, True]))
    detections, signals = [], []
    for arm in study.ARMS:
        for stage in ("early", "confirmed"):
            for i, time in zip((100, 124), times):
                hit = arm == "v3" or i == 100
                detections.append(dict(instrument="x", event_i=i, arm=arm, stage=stage, hit_1=hit, hit_6=hit,
                    htf_unknown_at_event=i == 100, htf_opposed_at_event=i == 124))
                if hit:
                    signals.append(dict(instrument="x", decision_i=i, decision_time=time, arm=arm,
                        stage=stage, event_id=arm + stage + str(i), match_status="matched",
                        htf_parent_unknown=i == 100, htf_parent_ready=i != 100))
    exposure = pd.DataFrame(dict(decision_time=times, eligible=[1, 1]))
    return labels, pd.DataFrame(detections), pd.DataFrame(signals), exposure


def test_d_cannot_shrink_original_positive_or_large_denominator():
    result = study.retention_summary(*fixture_tables())
    row = result[result.arm.eq("htf_gate") & result.stage.eq("early") & result.period.eq("full")].iloc[0]
    assert row.positive_events == 2 and row.large_positive_events == 2
    assert row.hits_1 == 1 and row.retention == .5 and row.large_recall_1 == .5
    assert row.distinct_labels == 1 and row.label_drop == .5
    assert row.same_time_removed == 1 and row.same_time_kept == 1


def test_missing_detection_is_error_not_a_smaller_population():
    labels, detections, signals, exposure = fixture_tables()
    detections = detections[~(detections.arm.eq("htf_gate") & detections.stage.eq("early") & detections.event_i.eq(124))]
    with pytest.raises(ValueError, match="fixed label denominator"):
        study.retention_summary(labels, detections, signals, exposure)


def test_upgrade_never_creates_additional_economic_position():
    _, _, signals, _ = fixture_tables()
    assert len(study.economic_parents(signals)) == 3
    duplicate = pd.concat([signals, signals.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate economic parent"):
        study.economic_parents(duplicate)


def p_tables():
    ab = pd.DataFrame(dict(arm=["reference", "near_box"], period=["full", "full"], permutation_p=[.04, .05]))
    c = pd.DataFrame(dict(arm=["confirmation_gate"], period=["full"], permutation_p=[.003]))
    d = pd.DataFrame(dict(arm=["formation_gate"], period=["full"], permutation_p=[.002]))
    e = pd.DataFrame(dict(arm=["htf_gate"], period=["full"], permutation_p=[.001]))
    return ab, c, d, e


def test_five_hypotheses_remain_in_holm_family():
    result = study.structural_holm(*p_tables()).set_index("arm")
    assert len(result) == 5
    assert result.loc["htf_gate", "holm_p_five"] == pytest.approx(.005)
    assert result.loc["confirmation_gate", "holm_p_five"] == pytest.approx(.009)
    assert result.loc["reference", "holm_p_five"] == pytest.approx(.08)
    assert result.loc["near_box", "holm_p_five"] == pytest.approx(.08)


def test_missing_historical_hypothesis_fails_closed():
    ab, c, d, e = p_tables()
    with pytest.raises(ValueError, match="Exactly one frozen"):
        study.structural_holm(ab, c.iloc[:0], d, e)


def test_missing_p_keeps_conservative_one_in_family():
    ab, c, d, e = p_tables()
    c.loc[0, "permutation_p"] = float("nan")
    result = study.structural_holm(ab, c, d, e).set_index("arm")
    assert len(result) == 5 and result.loc["confirmation_gate", "correction_input_p"] == 1
    assert result.loc["htf_gate", "holm_p_five"] == pytest.approx(.005)
    e.loc[0, "permutation_p"] = -1
    with pytest.raises(ValueError, match="within"):
        study.structural_holm(ab, c, d, e)


@pytest.mark.parametrize("key,value", [("tick", .02), ("features_sha256", "other"), ("minutes", 240)])
def test_cache_never_crosses_source_tick_or_timeframe(key, value):
    job = dict(instrument="x", features_sha256="frozen", tick=.01, minutes=60)
    signatures = {"x": study.job_signature(job)}
    study.assert_cache_signature(job, signatures)
    with pytest.raises(ValueError, match="source/tick/timeframe/cutoff"):
        study.assert_cache_signature(dict(job, **{key: value}), signatures)


def test_acceptance_cannot_trade_less_noise_for_missing_launches():
    retention = pd.DataFrame(dict(arm=["htf_gate"], stage=["early"], period=["full"],
        label_drop=[.9], distinct_labels=[1200], large_recall_1=[.2], retention=[.3]))
    trades = pd.DataFrame(dict(arm=["htf_gate"], period=["full"], mean_excess_bp=[10.], holm_p_five=[.2]))
    result = study.assessment(retention, trades)
    assert result["label_drop_pass"]
    assert not result["large_recall_pass"] and not result["baseline_retention_pass"] and not result["economic_pass"]
    assert not result["live_deployed"] and not result["accepted_for_deployment"]


def test_original_absolute_label_budget_is_preserved():
    retention = pd.DataFrame(dict(arm=["htf_gate"], stage=["early"], period=["full"],
        label_drop=[.6], distinct_labels=[6500], large_recall_1=[.9], retention=[.95]))
    trades = pd.DataFrame(dict(arm=["htf_gate"], period=["full"], mean_excess_bp=[10.], holm_p_five=[.005]))
    result = study.assessment(retention, trades)
    assert not result["label_drop_pass"]
    assert result["economic_pass"] and result["large_recall_pass"] and result["baseline_retention_pass"]


def test_unknown_is_kept_and_reported_separately_not_counted_as_approval():
    result = study.retention_summary(*fixture_tables())
    row = result[result.arm.eq("htf_gate") & result.stage.eq("early") & result.period.eq("full")].iloc[0]
    assert row.htf_unknown_signals == 1 and row.htf_known_signals == 0
    assert row.htf_unknown_positive_events == 1 and row.htf_opposed_positive_events == 1
    assert row.positive_events == 2 and row.retention == .5


def test_background_accounting_keeps_unknown_and_known_veto_separate():
    exposure = pd.DataFrame(dict(decision_time=pd.to_datetime(["2026-08-01T12:00Z"]),
        eligible=[10], htf_unknown_eligible=[4], htf_ready_eligible=[6],
        v3_early_unknown=[2], v3_early_opposed=[3], e_early_unknown=[2],
        e_early_known=[1], e_blocked_by_htf=[3]))
    row = study.background_summary(exposure).query("period == 'full'").iloc[0]
    assert row.htf_unknown_hours == 4 and row.htf_ready_hours == 6 and row.unknown_fraction == .4
    assert row.e_early_unknown == 2 and row.e_early_known == 1
    assert row.e_edges_blocked_by_known_opposition == 3
    exposure.loc[0, "htf_ready_eligible"] = 5
    with pytest.raises(ValueError, match="separate known/unknown"):
        study.background_summary(exposure)
