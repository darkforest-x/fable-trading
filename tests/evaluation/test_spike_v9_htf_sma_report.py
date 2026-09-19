"""Parameter selection cannot depend on later outcomes or crossing trades."""
import pandas as pd
import numpy as np

from yoyo.evaluation.spike_v9_htf_sma_report import select_lengths, assign_period, holm


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
