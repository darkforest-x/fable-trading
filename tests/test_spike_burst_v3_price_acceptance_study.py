"""Synthetic contracts for F label denominators, parent economics and Holm6."""
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_v3_price_acceptance_study as study


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
                    ))
                if hit:
                    signals.append(dict(instrument="x", decision_i=i, decision_time=time, arm=arm,
                        stage=stage, event_id=arm + stage + str(i), match_status="matched",
                        ))
    exposure = pd.DataFrame(dict(decision_time=times, eligible=[1, 1]))
    return labels, pd.DataFrame(detections), pd.DataFrame(signals), exposure


def test_d_cannot_shrink_original_positive_or_large_denominator():
    result = study.retention_summary(*fixture_tables())
    row = result[result.arm.eq("price_acceptance") & result.stage.eq("early") & result.period.eq("full")].iloc[0]
    assert row.positive_events == 2 and row.large_positive_events == 2
    assert row.hits_1 == 1 and row.retention == .5 and row.large_recall_1 == .5
    assert row.distinct_labels == 1 and row.label_drop == .5
    assert row.same_time_removed == 1 and row.same_time_kept == 1


def test_missing_detection_is_error_not_a_smaller_population():
    labels, detections, signals, exposure = fixture_tables()
    detections = detections[~(detections.arm.eq("price_acceptance") & detections.stage.eq("early") & detections.event_i.eq(124))]
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
    e = pd.DataFrame(dict(arm=["price_acceptance"], period=["full"], permutation_p=[.001]))
    e["arm"] = "htf_gate"
    f = pd.DataFrame(dict(arm=["price_acceptance"], period=["full"], permutation_p=[.0005]))
    return ab, c, d, e, f


def test_six_hypotheses_remain_in_holm_family():
    result = study.structural_holm(*p_tables()).set_index("arm")
    assert len(result) == 6
    assert result.loc["price_acceptance", "holm_p_six"] == pytest.approx(.003)
    assert result.loc["confirmation_gate", "holm_p_six"] == pytest.approx(.009)
    assert result.loc["reference", "holm_p_six"] == pytest.approx(.08)
    assert result.loc["near_box", "holm_p_six"] == pytest.approx(.08)


def test_missing_historical_hypothesis_fails_closed():
    ab, c, d, e, f = p_tables()
    with pytest.raises(ValueError, match="Exactly one frozen"):
        study.structural_holm(ab, c.iloc[:0], d, e, f)


def test_missing_p_keeps_conservative_one_in_family():
    ab, c, d, e, f = p_tables()
    c.loc[0, "permutation_p"] = float("nan")
    result = study.structural_holm(ab, c, d, e, f).set_index("arm")
    assert len(result) == 6 and result.loc["confirmation_gate", "correction_input_p"] == 1
    assert result.loc["price_acceptance", "holm_p_six"] == pytest.approx(.003)
    e.loc[0, "permutation_p"] = -1
    with pytest.raises(ValueError, match="within"):
        study.structural_holm(ab, c, d, e, f)


@pytest.mark.parametrize("key,value", [("tick", .02), ("features_sha256", "other"), ("minutes", 240)])
def test_cache_never_crosses_source_tick_or_timeframe(key, value):
    job = dict(instrument="x", features_sha256="frozen", tick=.01, minutes=60)
    signatures = {"x": study.job_signature(job)}
    study.assert_cache_signature(job, signatures)
    with pytest.raises(ValueError, match="source/tick/timeframe/cutoff"):
        study.assert_cache_signature(dict(job, **{key: value}), signatures)


def test_acceptance_cannot_trade_less_noise_for_missing_launches():
    retention = pd.DataFrame(dict(arm=["price_acceptance"], stage=["early"], period=["full"],
        label_drop=[.9], distinct_labels=[1200], large_recall_1=[.2], retention=[.3]))
    trades = pd.DataFrame(dict(arm=["price_acceptance"], period=["full"], mean_excess_bp=[10.], holm_p_six=[.2]))
    result = study.assessment(retention, trades, pd.DataFrame(dict(period=["full"],unknown_original_labels=[0])))
    assert result["label_drop_pass"]
    assert not result["large_recall_pass"] and not result["baseline_retention_pass"] and not result["economic_pass"]
    assert not result["live_deployed"] and not result["accepted_for_deployment"]


def test_original_absolute_label_budget_is_preserved():
    retention = pd.DataFrame(dict(arm=["price_acceptance"], stage=["early"], period=["full"],
        label_drop=[.6], distinct_labels=[6500], large_recall_1=[.9], retention=[.95]))
    trades = pd.DataFrame(dict(arm=["price_acceptance"], period=["full"], mean_excess_bp=[10.], holm_p_six=[.005]))
    result = study.assessment(retention, trades, pd.DataFrame(dict(period=["full"],unknown_original_labels=[0])))
    assert not result["label_drop_pass"]
    assert result["economic_pass"] and result["large_recall_pass"] and result["baseline_retention_pass"]




def clock_fixture():
    start = study.old.START
    h = pd.Timedelta(hours=1)
    rows = [
        dict(instrument="warm", candidate_time=start-h, raw_child_time=start, acceptance_time=start, candidate_status="accepted"),
        dict(instrument="merge", candidate_time=start+2*h, raw_child_time=start+3*h, acceptance_time=start+3*h, candidate_status="accepted"),
        dict(instrument="later", candidate_time=start+4*h, raw_child_time=start+7*h, acceptance_time=start+5*h, candidate_status="accepted"),
        dict(instrument="reject", candidate_time=start+9*h, raw_child_time=start+11*h, acceptance_time=pd.NaT, candidate_status="rejected"),
        dict(instrument="tail", candidate_time=study.old.END-h, raw_child_time=pd.NaT, acceptance_time=pd.NaT, candidate_status="unknown"),
    ]
    signals = []
    for row in rows:
        raw = {row['candidate_time']}
        if pd.notna(row['raw_child_time']): raw.add(row['raw_child_time'])
        for arm, times in [('v3', raw), ('price_acceptance', {max(t,row['acceptance_time']) for t in raw} if row['candidate_status']=='accepted' else set())]:
            for time in times:
                if start <= time < study.old.END:
                    signals.append(dict(arm=arm, instrument=row['instrument'], decision_time=time, decision_i=int(time.timestamp()/3600)))
    return pd.DataFrame(rows), pd.DataFrame(signals)


def test_clock_reconciliation_separates_unknown_shift_and_merging():
    row = study.clock_summary(*clock_fixture()).query("period == 'full'").iloc[0]
    assert row.raw_labels == 8 and row.accepted_public_labels == 4
    assert row.rejected_original_labels == 2 and row.unknown_original_labels == 1
    assert row.shift_in_labels == 1 and row.shift_out_labels == 0
    assert row.early_child_merges == 2
    assert row.candidates_by_discovery == 4 and row.acceptances_by_publication == 3
    assert row.accepted_shift_in == 1 and row.unknown_by_discovery == 1
    assert row.public_label_drop_excluding_unknown == 3/8


def test_fake_backdated_publication_breaks_accounting():
    candidates, signals = clock_fixture()
    signals.loc[signals.arm.eq('price_acceptance') & signals.instrument.eq('later'), 'decision_time'] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match='publication clocks'):
        study.clock_summary(candidates, signals)


def metadata_fixture():
    frame = pd.DataFrame(dict(close=[10.,11.,12.,13.,14.]), index=pd.date_range('2026-08-01', periods=5, freq='h', tz='UTC'))
    raw = pd.DataFrame(dict(early=[False,True,False,False,False], confirmed=[False,True,False,False,False], parent_i=[-1,1,1,1,1]))
    state = pd.DataFrame(dict(early=[False,False,True,False,False], confirmed=[False,False,True,False,False], parent_i=[-1,1,1,1,1], original_child_i=[float('nan'),float('nan'),1,float('nan'),float('nan')], acceptance_i=[float('nan'),float('nan'),2,2,2], publication_i=[float('nan'),float('nan'),2,float('nan'),float('nan')]))
    return frame, raw, state


def test_acceptance_economics_and_earlier_child_keep_distinct_clocks():
    frame, raw, state = metadata_fixture()
    row = study.event_metadata(frame,state,raw,'price_acceptance','confirmed',2)
    assert row['candidate_i'] == 1 and row['economic_parent_i'] == 2
    assert row['original_child_i'] == 1 and row['publication_i'] == 2
    assert row['candidate_close'] == 11 and row['publication_close'] == 12
    assert row['original_child_close'] == 11 and row['publication_delay_bars'] == 1
    assert row['publication_time'] == row['candidate_time'] + pd.Timedelta(hours=1)


def test_after_rejection_child_cannot_create_publication():
    frame, raw, state = metadata_fixture()
    state.loc[2,'early'] = False
    with pytest.raises(ValueError,match='accepted economic parent'):
        study.event_metadata(frame,state,raw,'price_acceptance','confirmed',2)


def test_unknown_must_not_help_the_primary_label_gate_pass():
    retention = pd.DataFrame(dict(arm=["price_acceptance"],stage=["early"],period=["full"],
        label_drop=[.51],distinct_labels=[6000],large_recall_1=[.9],retention=[.95]))
    trades = pd.DataFrame(dict(arm=["price_acceptance"],period=["full"],mean_excess_bp=[10.],holm_p_six=[.005]))
    row = study.assessment(retention,trades,pd.DataFrame(dict(period=["full"],unknown_original_labels=[30])))
    assert row['raw_distinct_labels'] == 6000 and row['conservative_labels'] == 6030
    assert not row['label_drop_pass'] and row['economic_pass']
