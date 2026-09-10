"""Boundary tests for the history wrapper, not redundant strategy tests."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_burst_long_history import annual_rows, cohort_rows, _score, sha


def test_annual_nav_inherits_boundary_equity_and_counts_exit_clock():
    cuts = list(pd.to_datetime(['2024-01-01','2024-01-02','2024-01-03'], utc=True))
    curve = pd.DataFrame({'time':pd.to_datetime(['2024-01-01 12:00','2024-01-02 00:00',
                                              '2024-01-02 12:00','2024-01-03 00:00'],utc=True),
                          'equity':[110000.,120000.,108000.,114000.]})
    ledger = pd.DataFrame([dict(portfolio_selected=True,entry_time=cuts[0],
                                exit_time=cuts[2],realized_net_pnl=14000.,natural_exit=True)])
    rows=annual_rows(curve,ledger,cuts)
    assert rows[0]['return_pct']==pytest.approx(20.)
    assert rows[1]['opening_equity']==120000.
    assert rows[1]['return_pct']==pytest.approx(-5.)
    assert rows[1]['max_drawdown_pct']==pytest.approx(10.)
    assert rows[0]['exits']==0 and np.isnan(rows[0]['win_rate'])
    assert rows[1]['exits']==1 and rows[1]['win_rate']==1
    # A winning eventual exit cannot be used to label the first year's trades.
    assert rows[0]['natural_exits']==0


def test_majors_do_not_leak_into_altcoin_primary_cohort():
    rows=pd.DataFrame({'asset':['BTC','ETH','SOL','1000PEPE'], 'event_id':[1,2,3,4]})
    assert cohort_rows(rows,'altcoins').event_id.tolist()==[3,4]
    assert cohort_rows(rows,'BTC').event_id.tolist()==[1]
    assert cohort_rows(rows,'ETH').event_id.tolist()==[2]


def test_scoring_partition_cannot_add_or_move_parent_candidate(tmp_path):
    parent=dict(instrument='binance:ETHUSDT:segment0',minutes=60,decisions={'burst_trail':[350]})
    path=tmp_path/'matching.json'
    path.write_text(json.dumps({'jobs':[parent],'schedule_artifacts':[]}))
    altered=dict(parent,decisions={'burst_trail':[351]})
    with pytest.raises(ValueError,match='exact frozen parent job'):
        _score(altered,tmp_path,sha(path))
    assert not (tmp_path/'scored').exists()
