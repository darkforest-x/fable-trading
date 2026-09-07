"""SQL reports preserve empty-fold means as unknown and fee decomposition."""
import sqlite3
import pandas as pd

from yoyo.evaluation.owner_k1k2_flow_report import QUERIES


def test_fold_grid_does_not_turn_no_trades_into_zero_average():
    f=pd.DataFrame([dict(event_id="a",fold="2023H1",closed=True,flow_pass=True,
        gross_return=.001,net_return=-.001)])
    with sqlite3.connect(":memory:") as c:
        f.to_sql("case_trades",c,index=False)
        r=pd.read_sql_query(QUERIES["folds"],c)
    assert len(r)==8
    assert (r.loc[r.fold.eq("2024H2"),"trades"]==0).all()
    assert r.loc[r.fold.eq("2024H2"),"net_bp"].isna().all()
    assert (r.loc[r.fold.eq("2023H1"),"net_bp"]==-10).all()


def test_failure_counts_preserve_winners_removed_by_flow():
    f=pd.DataFrame([dict(flow_pass=0,failure_class="net_winner",outcome="colour_exit",net_return=.004,
        hold_minutes=20,gave_back_fee_covering_peak=False),
        dict(flow_pass=1,failure_class="fee_erased_gross_gain",outcome="colour_exit",net_return=-.001,
        hold_minutes=5,gave_back_fee_covering_peak=False)])
    with sqlite3.connect(":memory:") as c:
        f.to_sql("case_trades",c,index=False)
        r=pd.read_sql_query(QUERIES["failures"],c)
    assert r.trades.sum()==2
    removed=r.loc[r.flow_pass.eq(0)].iloc[0]
    assert removed.failure_class=="net_winner" and removed.mean_net_bp==40
