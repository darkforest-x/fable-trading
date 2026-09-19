"""Parameter selection cannot depend on later outcomes or crossing trades."""
import pandas as pd
import numpy as np

from yoyo.evaluation.spike_v9_htf_sma_report import select_lengths, assign_period, holm, monthly_delta, paired_controls


def test_selection_ignores_validation_and_common_scope():
    rows=[]
    for scope in ("actual","common"):
        for period in ("earlier","later"):
            for n in (0,50,120):
                rows.append(dict(scope=scope,period=period,direction="both",timeframe="15m",length=n,
                                 net_r={0:3,50:5,120:4}[n] if period=="earlier" and scope=="actual" else n*100))
    data=pd.DataFrame(rows)
    assert select_lengths(data)=={"15m":50}
    data.loc[data.period.eq("later"),"net_r"] = -999999
    assert select_lengths(data)=={"15m":50}
    data.loc[data.scope.eq("actual")&data.period.eq("earlier"),"net_r"]=0
    assert select_lengths(data)=={"15m":0}


def test_cross_split_is_not_development_evidence():
    frame=pd.DataFrame({"entry_time":["2025-08-01Z","2025-08-01Z","2025-09-10Z"],
                        "exit_time":["2025-09-01Z","2025-09-10Z","2025-09-12Z"]})
    frame=frame.replace("Z","T00:00:00Z",regex=True)
    assert assign_period(frame,pd.Timestamp("2025-09-10T00:00:00Z")).tolist()==["earlier","cross_split","later"]


def test_holm_monotonic_and_bounded():
    np.testing.assert_allclose(holm(np.array([.03,.001,.8])),[.06,.003,.8])


def test_month_sign_flip_has_known_exact_resolution():
    cfg={"start":"2024-09-10Z","split":"2025-09-10Z","end":"2026-05-01Z","seed":92026,"bootstrap_reps":4000}
    cfg={k:v.replace("Z","T00:00:00Z") if isinstance(v,str) else v for k,v in cfg.items()}
    times=pd.date_range("2025-09-15",periods=8,freq="MS",tz="UTC")-pd.Timedelta(days=1)
    a=pd.DataFrame({"entry_time":times,"net_r":np.zeros(8)})
    b=a.assign(net_r=1.)
    with np.errstate(all="raise"):
        got=monthly_delta(a,b,"later",cfg)
    assert got["p"]==1/256 and got["minimum_exact_p"]==1/256
    assert got["nonzero_months"]==8 and got["delta"]==8
    assert got["low95"]==8 and got["high95"]==8
    assert holm(np.array([got["p"]]*3)).min()>.01


def test_control_outcome_cannot_cross_development_boundary():
    t=pd.DataFrame({"censored":[False]*3,"matched":[True]*3,"control_censored":[False]*3,
                    "net_r":[1.,2.,3.],"control_net_r":[0.,50.,100.],
                    "control_entry_time":["2025-09-01","2025-09-01","2025-09-11"],
                    "control_exit_time":["2025-09-02","2025-09-11","2025-09-12"]})
    split=pd.Timestamp("2025-09-10",tz="UTC")
    assert paired_controls(t,split,"earlier")["random_excess_r"]==1.
    assert paired_controls(t,split,"cross_split")["random_excess_r"]==-48.
    assert paired_controls(t,split,"later")["random_excess_r"]==-97.
    assert paired_controls(t,split,"full")["random_pairs"]==3
