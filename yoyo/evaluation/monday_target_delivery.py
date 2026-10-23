"""Audit target-only differences and export existing results without replay."""
from __future__ import annotations
from importlib.metadata import version
from pathlib import Path
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_target_study import EXP,CODE,body,target_pairs
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp


def validate():
    counts=dict(positions_checked=0,near_stop_invariance=0,native_fills=0)
    inputs={};prior_stamps=[];done=[]
    for path in (EXP/'symbols').glob('*/done.json'):
        d=json.loads(path.read_text());done.append(d)
        folder=path.parent;identity=json.loads((folder/'identity.json').read_text())
        assert identity['code']==[file_stamp(ROOT/p) for p in CODE]
        for stamp in identity['inputs']:
            assert file_stamp(stamp['path'])==stamp
            inputs[stamp['path']]=stamp
        if identity['prior_ledger']:
            assert file_stamp(identity['prior_ledger']['path'])==identity['prior_ledger']
            prior_stamps.append(identity['prior_ledger'])
        ledger=folder/'trades.csv.gz'
        if not ledger.exists():continue
        f=pd.read_csv(ledger,float_precision='round_trip')
        assert not f.duplicated(['config_id','symbol','trade_id']).any()
        near=f[f.target_mode.eq('near')];far=f[f.target_mode.eq('far')]
        p=near.merge(far,on=['symbol','confirmations','trade_id','population'],suffixes=('_near','_far'),validate='one_to_one')
        assert len(p)==len(near)==len(far)
        for col in ['entry_time','entry_i','side','stop','entry','risk','pair_id','bucket','week','source_segment']:
            assert p[col+'_near'].equals(p[col+'_far']),col
        assert (p.side_near*(p.target_price_far-p.target_price_near)>0).all()
        assert (p.exit_original_i_far>=p.exit_original_i_near).all()
        sl=p[p.reason_near.eq(1)]
        assert sl.reason_far.eq(1).all()
        np.testing.assert_array_equal(sl.exit_original_i_near,sl.exit_original_i_far)
        np.testing.assert_allclose(sl.exit_price_near,sl.exit_price_far,rtol=1e-10,atol=1e-10)
        counts['near_stop_invariance']+=len(sl);counts['positions_checked']+=len(p)
        censored=p[~p.closed_near]
        assert not censored.closed_far.any()
        a=f[f.population.eq('signal')]
        assert (a.mfe_r+1e-8>=a.gross_r).all() and (a.mae_r+1e-8>=-a.gross_r).all()
        assert (a.mfe_r<=a.planned_rr+1e-7).all()
        crypto=f[~f.symbol.eq('XAUUSD')]
        np.testing.assert_allclose(crypto.gross_r-crypto.net_r,crypto.fee_r,rtol=1e-9,atol=1e-9)
        np.testing.assert_allclose(crypto.fee_r,(crypto.entry*crypto.fee_entry_rate+crypto.exit_price*crypto.fee_exit_rate)/crypto.risk,rtol=1e-9,atol=1e-9)
        assert f[f.symbol.eq('XAUUSD')].net_r.isna().all()
        native=folder/'native-fills.csv.gz'
        fills=len(pd.read_csv(native,usecols=['trade_key'])) if native.exists() else 0
        assert fills==2*d['new_positions'];counts['native_fills']+=fills
    cfg=json.loads((EXP/'config.json').read_text());jobs,_=body.previous.old.jobs(cfg)
    assert {d['symbol'] for d in done}=={j[0] for j in jobs}
    m=pd.read_csv(EXP/'metrics.csv');old=pd.read_csv(body.EXP/'metrics.csv')
    old=old[old.config_id.str.startswith('body-')].copy()
    old['config_id']=old.config_id.str.replace('body-','near-',regex=False)
    joined=m[m.target_mode.eq('near')].merge(old,on=['config_id','group','period'],suffixes=('_new','_old'),validate='one_to_one')
    assert len(joined)==75
    for col in ['signals','closed','censored','win_rate','mean_r','total_r','mean_bp','profit_factor',
                'paired_signal_r','paired_random_r','sl_after_positive_mfe','sl_after_one_r']:
        np.testing.assert_allclose(joined[col+'_new'],joined[col+'_old'],rtol=1e-9,atol=1e-9,equal_nan=True)
    result=dict(status='passed',symbols=len(done),near_metric_rows=len(joined),target_tests=7,
        new_far_positions=sum(d['new_positions'] for d in done),**counts,
        checks=['frozen near metrics','identical entries/stops/control times','farther targets only',
                'unchanged near-stop outcomes','far exits cannot precede near exits','censor monotonicity',
                'excursion truncation','per-fill fee arithmetic','two native fills per new position',
                'full universe','unchanged input/cache/code stamps'],training_eligible=False,production_eligible=False)
    dump(EXP/'validation.json',result)
    dump(EXP/'completion.json',dict(config=cfg,code=[file_stamp(ROOT/p) for p in CODE+[str(Path(__file__).relative_to(ROOT))]],
        inputs=list(inputs.values()),prior_ledgers=prior_stamps,versions={p:version(p) for p in ('vectorbt','numpy','pandas','numba')},
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),validation=result,
        reproduction=['./scripts/backtest-monday-targets --workers 3','/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_target_delivery']))
    return result


def export():
    f=pd.read_csv(EXP/'n3-trades.csv.gz',float_precision='round_trip')
    p=target_pairs(f)
    p['方向']=p.side_near.map({1:'多',-1:'空'})
    p['分组']=p.symbol.map(lambda s:'第一组' if s in ['BTCUSDT','ETHUSDT','XAUUSD'] else '第二组山寨')
    p['symbol']=p.symbol.astype(str)
    out=p[['分组','symbol','方向','pair_id','pin_time_near','entry_time_near','entry_near','stop_near']].rename(columns={
        'symbol':'品种','pair_id':'配对键','pin_time_near':'插针开始UTC','entry_time_near':'入场UTC','entry_near':'入场价','stop_near':'原插针止损价'})
    out['入场北京时间']=pd.to_datetime(p.entry_time_near,utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    for mode,label in [('near','近侧'),('far','远侧')]:
        mapping={'target_price':'止盈价','planned_rr':'计划盈亏比','exit_price':'实际出场价','exit_interval_start':'出场5m开始UTC',
            'gross_r':'毛R','net_r':'净R','gross_bp':'毛价格收益bp','net_bp':'净价格收益bp','fee_r':'手续费R',
            'holding_hours':'持仓小时约数','mfe_r':'最大毛浮盈R','mae_r':'最大毛浮亏R','mfe_time':'最大浮盈所在5m开始UTC'}
        for key,title in mapping.items():out[label+title]=p[key+'_'+mode]
        out[label+'出场类型']=p['reason_'+mode].map({0:'数据边界未决，不计胜负',1:'止损',4:'止盈'})
    out['两边均已判定']=p.closed_near&p.closed_far
    out['远减近净R']=np.where(out['两边均已判定'],p.net_r_far-p.net_r_near,np.nan)
    out['远减近毛R']=np.where(out['两边均已判定'],p.gross_r_far-p.gross_r_near,np.nan)
    out.to_csv(EXP/'逐笔近远止盈对照.csv',index=False,encoding='utf-8-sig')
    m=pd.read_csv(EXP/'metrics.csv');m['target_mode']=m.target_mode.map({'near':'近侧','far':'远侧'})
    m['period']=m.period.map({'all':'全期','earlier':'2023年前选参','later':'2023年起检验'})
    cols={'target_mode':'止盈目标','confirmations':'确认根数','group':'组或品种','period':'区间','basis':'毛净口径',
        'closed':'已判定笔数','censored':'未决笔数','win_rate':'胜率','mean_r':'平均每笔R','total_r':'独立交易合计R',
        'mean_bp':'平均价格收益bp','mean_win_r':'平均盈利R','mean_loss_r':'平均亏损R','realized_payoff_ratio':'实际盈亏比',
        'planned_rr_median':'计划盈亏比中位','profit_factor':'利润因子PF','paired':'配对样本数','paired_signal_r':'配对信号R',
        'paired_random_r':'配对随机R','sl':'止损笔数','tp':'止盈笔数','sl_after_positive_mfe':'毛浮盈后止损',
        'sl_after_half_r':'曾0.5R后止损','sl_after_one_r':'曾1R后止损','sl_after_two_r':'曾2R后止损',
        'holding_hours_median':'持仓小时中位','holding_hours_p90':'持仓小时P90'}
    m[list(cols)].rename(columns=cols).to_csv(EXP/'近远止盈与参数统计.csv',index=False,encoding='utf-8-sig')
    return dict(paired_n3_rows=len(out),metric_rows=len(m))


if __name__=='__main__':
    print(json.dumps(validate()),flush=True)
    print(json.dumps(export()),flush=True)
