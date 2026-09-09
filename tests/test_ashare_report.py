"""Reporting guards: complete results, natural exits and independent metadata."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.ashare_report import data_audit, natural_trades, choose_cases, write_report


def test_report_refuses_incomplete_research_before_any_rendering(tmp_path):
    with pytest.raises(ValueError, match='final_complete.json'):
        write_report(tmp_path/'data', tmp_path/'results', tmp_path/'report.md')
    assert not (tmp_path/'report.md').exists()


def test_case_selection_keeps_winners_and_losers_excludes_terminal_valuations():
    frame=pd.DataFrame([
        dict(code=str(i), entry_date='2024-01-02', reason=reason, is_terminal=terminal, return_net=value)
        for i,(reason,terminal,value) in enumerate([
            ('trend_close_exit',False,.6), ('trend_close_exit',False,.2),
            ('initial_stop',False,-.1), ('initial_stop',False,-.3),
            ('period_end_valuation',True,4.), ('terminal_stale_valuation',True,-1.),
        ])
    ])
    assert natural_trades(frame).code.tolist()==['0','1','2','3']
    cases=choose_cases(frame)
    assert [row.code for _,row in cases]==['0','1','3','2']


def test_listing_audit_reads_separate_sidecar_not_universe_survival(tmp_path):
    (tmp_path/'daily').mkdir()
    for code in ('sh.600000','sh.600001'):
        pd.DataFrame([dict(date='2024-01-02',tradestatus=1,isST=0,volume=10)]).to_csv(
            tmp_path/'daily'/f'{code}.csv',index=False)
    (tmp_path/'listing_metadata.json').write_text(json.dumps({'records':[
        {'code':'sh.600000','ipoDate':'1999-01-01','outDate':'2024-12-01'},
        {'code':'sh.600001','error':'provider metadata incomplete'},
        {'code':'sh.000300','ipoDate':'2005-01-01','outDate':''},
    ]}))
    audit=data_audit(tmp_path,{'codes':['sh.600000','sh.600001','sh.600002']})
    assert audit['delisted_by_end']==[{'code':'sh.600000','outDate':'2024-12-01'}]
    assert audit['listing_metadata_missing']==['sh.600002']
    assert len(audit['listing_metadata_errors'])==1
    assert audit['missing']==['sh.600002']


def test_listing_audit_requires_metadata_evidence(tmp_path):
    with pytest.raises(ValueError,match='Separate listing metadata'):
        data_audit(tmp_path,{'codes':['sh.600000']})
