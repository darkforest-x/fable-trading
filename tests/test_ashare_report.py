"""Reporting guards: complete results, natural exits and independent metadata."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.ashare_report import (
    data_audit, natural_trades, choose_cases, write_report, read_exclusions,
    engineering_validation, risk_distribution,
)


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
    assert audit['listing_metadata_missing']==['sh.600001','sh.600002']
    assert len(audit['listing_metadata_errors'])==1
    assert audit['missing']==['sh.600002']


def test_listing_audit_requires_metadata_evidence(tmp_path):
    with pytest.raises(ValueError,match='Separate listing metadata'):
        data_audit(tmp_path,{'codes':['sh.600000']})


def test_quality_exclusions_remain_in_pool_but_not_research_counts(tmp_path):
    (tmp_path/'daily').mkdir()
    codes=['sh.600000','sh.600001','sz.001914','sh.600321']
    records=[dict(date='2021-01-04',tradestatus=1,isST=0,volume=10),
             dict(date='2023-01-04',tradestatus=0,isST=1,volume=0),
             dict(date='2024-01-04',tradestatus=1,isST=1,volume=20)]
    for code in codes[:2]:
        pd.DataFrame(records).to_csv(tmp_path/'daily'/f'{code}.csv',index=False)
    (tmp_path/'listing_metadata.json').write_text(json.dumps({'records':[{'code':c} for c in codes]}))
    (tmp_path/'exclusions.json').write_text(json.dumps({'codes':{
        'sh.600001':'corporate action cannot be reconciled','sz.001914':'mixed adjustment flags'}}))
    audit=data_audit(tmp_path,{'codes':codes})
    assert audit['selected']==4 and audit['loaded']==2
    assert audit['research_available']==1
    assert audit['rows']==6 and audit['research_rows']==3
    assert audit['fold_rows']=={'development':2,'validation':0,'final':2}
    assert audit['research_fold_rows']=={'development':1,'validation':0,'final':1}
    assert audit['research_st_rows']==2 and audit['research_suspended_rows']==1
    assert audit['missing']==['sz.001914','sh.600321']
    assert audit['unexplained_missing']==['sh.600321']


@pytest.mark.parametrize('codes',[
    {'sh.600099':'outside pool'}, {'sh.600000':''}, {'sh.600000':{}}, ['sh.600000'],
])
def test_quality_exclusions_require_in_pool_explained_records(tmp_path,codes):
    (tmp_path/'exclusions.json').write_text(json.dumps({'codes':codes}))
    with pytest.raises(ValueError,match='exclusion'):
        read_exclusions(tmp_path,{'codes':['sh.600000']})


def test_engineering_validation_reports_current_record_and_failures(tmp_path):
    assert '未提供' in engineering_validation(tmp_path)
    (tmp_path/'engineering_validation.json').write_text(json.dumps({
        'generated_at':'2026-09-09','source_commit':'verified-commit',
        'checks':[{'name':'Dedicated checks','passed':101,'failed':0,'skipped':2,'command':'pytest selected'},
                  {'name':'Known boundary failure','passed':85,'failed':1,'notes':'pre-existing'}]}))
    text=engineering_validation(tmp_path)
    assert '| Dedicated checks | 101 | 0 | 2 | pytest selected |' in text
    assert '| Known boundary failure | 85 | 1 | 0 |' in text
    assert 'verified-commit' in text and '76' not in text


@pytest.mark.parametrize('count',[-1,1.5,True])
def test_engineering_validation_rejects_invalid_counts(tmp_path,count):
    (tmp_path/'engineering_validation.json').write_text(json.dumps({'checks':[{'name':'test','passed':count}]}))
    with pytest.raises(ValueError,match='nonnegative integers'):
        engineering_validation(tmp_path)


def test_peak_r_and_realized_r_keep_distinct_denominators_and_natural_scope():
    frame=pd.DataFrame([
        dict(reason='trend_close_exit',is_terminal=False,entry=100,risk=10,risk_cash=1000,
             pnl=1800,peak_r=5,holding_days=20),
        dict(reason='initial_stop',is_terminal=False,entry=100,risk=20,risk_cash=2000,
             pnl=-2200,peak_r=0,holding_days=2),
        dict(reason='period_end_valuation',is_terminal=True,entry=100,risk=60,risk_cash=6000,
             pnl=60000,peak_r=20,holding_days=500),
    ])
    rows=risk_distribution(frame)
    assert rows[0]['n']==3 and rows[0]['median']==pytest.approx(.2)
    assert rows[1]['n']==2 and rows[1]['median']==11
    assert rows[2]['median']==2.5
    assert rows[3]['median']==pytest.approx(.35)
    assert rows[4]['median']==pytest.approx((3.2+1.1)/2)


def test_final_listing_evidence_supersedes_historical_network_failure(tmp_path):
    from yoyo.evaluation.ashare_report import digest
    data=tmp_path/'data';(data/'daily').mkdir(parents=True)
    codes=['sh.601188','sh.600321']
    universe={'codes':codes}
    (data/'universe.json').write_text(json.dumps(universe))
    (data/'exclusions.json').write_text(json.dumps({'codes':{'sh.600321':'corporate-action anomaly'}}))
    source=tmp_path/'recovered-basic.csv';source.write_text('verified source')
    (data/'listing_metadata.json').write_text(json.dumps({'records':[
        {'code':'sh.601188','error':'historical login failure'}, {'code':'sh.600321','outDate':'2024-06-27'}]}))
    for code in codes:
        pd.DataFrame([dict(date='2024-01-02',tradestatus=1,isST=0,volume=10)]).to_csv(
            data/'daily'/f'{code}.csv',index=False)
    final=dict(complete=True,data_directory=str(data),universe_sha256=digest(data/'universe.json'),
               exclusions_sha256=digest(data/'exclusions.json'),source_receipt_count=6,
               all_source_hashes_verified=True,research_usable_stock_count=1,research_usable_rows=1,
               research_traded_rows=1,listing_metadata=[
                   dict(code=code,outDate='2024-06-27' if code=='sh.600321' else '',metadata_complete=True,
                        source=str(source),source_sha256=digest(source)) for code in codes])
    (tmp_path/'mainboard_data_audit.json').write_text(json.dumps(final))
    audit=data_audit(data,universe)
    assert audit['listing_metadata_missing']==[]
    assert audit['listing_metadata_errors']==[]
    assert audit['listing_metadata_historical_errors'][0]['code']=='sh.601188'
    assert audit['delisted_by_end']==[{'code':'sh.600321','outDate':'2024-06-27'}]
    assert audit['research_delisted_by_end']==[]
    source.write_text('changed after final audit')
    with pytest.raises(ValueError,match='source is missing or changed'):
        data_audit(data,universe)
