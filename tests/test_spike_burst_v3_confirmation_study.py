"""Contracts for separate coverage clocks, parent economics and multiplicity."""
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_v3_confirmation_study as study


def fixture_tables():
    times = pd.to_datetime(["2026-08-01T12:00Z", "2026-08-02T12:00Z"])
    labels = pd.DataFrame(dict(instrument=["x", "x"], event_i=[100, 124],
        decision_time=times, label=["positive", "positive"], large_peak=[True, True]))
    detections, signals = [], []
    for arm in study.ARMS:
        for stage in ("early", "confirmed"):
            for i, time in zip((100, 124), times):
                hit = arm == "v3" or i == 100
                detections.append(dict(instrument="x", event_i=i, arm=arm, stage=stage,
                    hit_1=hit, hit_6=hit, already_tracking=not hit,
                    suppression_already_tracking=False))
                if hit:
                    signals.append(dict(instrument="x", decision_i=i, decision_time=time,
                        arm=arm, stage=stage, event_id=arm + stage + str(i), match_status="matched"))
    exposure = pd.DataFrame(dict(decision_time=times, eligible=[1, 1]))
    return labels, pd.DataFrame(detections), pd.DataFrame(signals), exposure


def test_temporary_reference_coverage_is_not_committed_suppression_or_fresh_retention():
    tables = fixture_tables()
    result = study.retention_summary(*tables)
    row = result[result.arm.eq("confirmation_gate") & result.stage.eq("early") & result.period.eq("full")].iloc[0]
    assert row.positive_events == 2 and row.hits_1 == 1
    assert row.retention == .5 and row.recall_1 == .5
    assert row.reference_tracking_positive_events == 1
    assert row.suppression_tracking_positive_events == 0
    assert row.distinct_labels == 1 and row.label_drop == .5


def test_even_committed_suppression_cannot_repair_missing_fresh_signal():
    labels, detections, signals, exposure = fixture_tables()
    detections.loc[detections.arm.eq("confirmation_gate") & detections.event_i.eq(124), "suppression_already_tracking"] = True
    result = study.retention_summary(labels, detections, signals, exposure)
    row = result[result.arm.eq("confirmation_gate") & result.stage.eq("early") & result.period.eq("full")].iloc[0]
    assert row.suppression_tracking_positive_events == 1
    assert row.hits_1 == 1 and row.retention == .5


def test_missing_detection_cannot_silently_shrink_denominator():
    labels, detections, signals, exposure = fixture_tables()
    detections = detections[~(detections.arm.eq("confirmation_gate") & detections.stage.eq("early") & detections.event_i.eq(124))]
    with pytest.raises(ValueError, match="fixed label denominator"):
        study.retention_summary(labels, detections, signals, exposure)


def test_same_bar_or_delayed_upgrade_is_not_an_extra_economic_trade():
    _, _, signals, _ = fixture_tables()
    parents = study.economic_parents(signals)
    assert len(parents) == 3 and parents.stage.eq("early").all()
    duplicate = pd.concat([signals, parents.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate economic parent"):
        study.economic_parents(duplicate)


def test_holm_keeps_prior_two_hypotheses_in_the_family():
    prior = pd.DataFrame(dict(arm=["reference", "near_box"], period=["full", "full"], permutation_p=[.02, .04]))
    current = pd.DataFrame(dict(arm=["confirmation_gate"], period=["full"], permutation_p=[.001]))
    result = study.structural_holm(prior, current).set_index("arm")
    assert result.loc["confirmation_gate", "holm_p_three"] == pytest.approx(.003)
    assert result.loc["reference", "holm_p_three"] == pytest.approx(.04)
    assert result.loc["near_box", "holm_p_three"] == pytest.approx(.04)
    with pytest.raises(ValueError, match="Exactly one frozen"):
        study.structural_holm(prior.iloc[:1], current)


def test_missing_prior_p_is_conservative_not_removed_from_family():
    prior = pd.DataFrame(dict(arm=["reference", "near_box"], period=["full", "full"], permutation_p=[float("nan"), .04]))
    current = pd.DataFrame(dict(arm=["confirmation_gate"], period=["full"], permutation_p=[.001]))
    result = study.structural_holm(prior, current).set_index("arm")
    assert len(result) == 3 and result.loc["reference", "correction_input_p"] == 1
    assert result.loc["confirmation_gate", "holm_p_three"] == pytest.approx(.003)


@pytest.mark.parametrize("key,value", [("tick", .02), ("features_sha256", "other"), ("minutes", 240)])
def test_cache_identity_does_not_cross_tick_source_or_timeframe(key, value):
    job = dict(instrument="x", features_sha256="frozen", tick=.01, minutes=60)
    signatures = {"x": study.job_signature(job)}
    study.assert_cache_signature(job, signatures)
    changed = dict(job, **{key: value})
    with pytest.raises(ValueError, match="source/tick/timeframe/cutoff"):
        study.assert_cache_signature(changed, signatures)


def test_covering_reference_and_label_reduction_alone_do_not_pass_acceptance():
    retention = pd.DataFrame(dict(arm=["confirmation_gate"], stage=["early"], period=["full"],
        label_drop=[.9], large_recall_1=[.2], retention=[.3]))
    trades = pd.DataFrame(dict(arm=["confirmation_gate"], period=["full"], mean_excess_bp=[10.], holm_p_three=[.2]))
    result = study.assessment(retention, trades)
    assert result["label_drop_pass"] is True
    assert result["large_recall_pass"] is False and result["baseline_retention_pass"] is False
    assert result["economic_pass"] is False and result["deployment_authorized"] is False
