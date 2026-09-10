"""Guard against calling held-reference coverage preserved fresh signals."""
import pandas as pd
from yoyo.evaluation import spike_burst_v3_focus_study as study


def test_held_coverage_never_repairs_missing_new_signal_retention():
    times=pd.to_datetime(['2026-08-01T12:00Z','2026-08-02T12:00Z'])
    labels=pd.DataFrame(dict(instrument=['x','x'],event_i=[100,124],decision_time=times,
                             label=['positive','positive'],large_peak=[True,True]))
    det=[];signals=[]
    for arm in study.ARMS:
        for stage in ('early','confirmed'):
            for i,t in zip((100,124),times):
                hit=arm=='v3' or i==100
                det.append(dict(instrument='x',event_i=i,arm=arm,stage=stage,
                                hit_1=hit,hit_6=hit,already_tracking=not hit))
                if hit:signals.append(dict(instrument='x',decision_i=i,decision_time=t,
                                          arm=arm,stage=stage,match_status='matched'))
    exposure=pd.DataFrame(dict(decision_time=times,eligible=[1,1]))
    result=study.retention_summary(labels,pd.DataFrame(det),pd.DataFrame(signals),exposure)
    r=result[result.arm.eq('reference')&result.stage.eq('early')&result.period.eq('full')].iloc[0]
    assert r.retention==.5 and r.recall_1==.5
    assert r.tracking_positive_events==1
    assert r.distinct_labels==1  # Parent+child on same bar counts once.
    assert r.label_drop==.5
    assert r.same_time_removed==1 and r.newly_caught==0
