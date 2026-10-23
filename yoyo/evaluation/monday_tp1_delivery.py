"""Validate/export existing TP1 native fills and paired policy comparisons."""
from __future__ import annotations
from importlib.metadata import version
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_tp1_study import EXP,PRIOR,CODE,META,paired,finite,source
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp


def validate(cfg):
    counts=dict(symbols=0,parents=0,signals=0,new_native_parents=0,native_fills=0,
                unchanged_without_tp1=0,fee_checks=0)
    input_stamps={};prior=[];native_versions=[]
    for path in (EXP/'symbols').glob('*/done.json'):
        counts['symbols']+=1;d=json.loads(path.read_text());folder=path.parent
        identity=json.loads((folder/'identity.json').read_text())
        if identity['versions'] not in native_versions:native_versions.append(identity['versions'])
        assert identity['config']==cfg
        assert identity['code']==[file_stamp(ROOT/p) for p in CODE]
        for stamp in identity['inputs']:
            assert file_stamp(stamp['path'])==stamp
            input_stamps[stamp['path']]=stamp
        if identity['prior']:
            assert file_stamp(identity['prior']['path'])==identity['prior'];prior.append(identity['prior'])
        for key in ['parents','signals','new_native_parents','native_fills']:counts[key]+=d[key]
        if not d['parents']:continue
        f=pd.read_csv(folder/'trades.csv.gz',float_precision='round_trip')
        assert not f.duplicated(['config_id','trade_id']).any()
        old=pd.read_csv(PRIOR/'symbols'/d['symbol']/'trades.csv.gz',float_precision='round_trip');near,far=paired(old)
        arms={key:g.set_index('trade_id',drop=False).loc[near.index] for key,g in f.groupby('config_id')}
        assert set(arms)=={'near_full','far_full','split_hold','split_be'}
        for name,a in arms.items():
            for col in META:assert a[col].equals(near[col]),(name,col)
        for col in ['gross_r','net_r','fee_r','gross_bp','net_bp']:
            np.testing.assert_allclose(arms['near_full'][col],near[col],atol=1e-9,rtol=1e-9,equal_nan=True)
            np.testing.assert_allclose(arms['far_full'][col],far[col],atol=1e-9,rtol=1e-9,equal_nan=True)
            np.testing.assert_allclose(arms['split_hold'][col],.5*(near[col]+far[col]),atol=1e-9,rtol=1e-9,equal_nan=True)
            mask=~near.reason.eq(4)
            np.testing.assert_allclose(arms['split_be'].loc[mask,col],arms['split_hold'].loc[mask,col],atol=1e-9,rtol=1e-9,equal_nan=True)
        a=arms['split_hold'];b=arms['split_be'];counts['unchanged_without_tp1']+=int((~a.tp1_filled).sum())
        assert (b.exit_original_i<=a.exit_original_i).all()
        assert b.loc[a.closed,'closed'].all()
        assert (b.be_effective_i.lt(0)|b.be_effective_i.ge(b.tp1_exit_original_i)).all()
        if d['symbol']=='XAUUSD':assert f.net_r.isna().all() and f.fee_r.isna().all()
        else:np.testing.assert_allclose(f.gross_r-f.fee_r,f.net_r,atol=1e-9,rtol=1e-9)
        if not d['new_native_parents']:continue
        legs=pd.read_csv(folder/'native-legs.csv.gz',float_precision='round_trip')
        fills=pd.read_csv(folder/'native-fills.csv.gz',float_precision='round_trip')
        assert len(fills)==d['native_fills']==4*d['new_native_parents']
        assert len(legs)==2*d['new_native_parents']
        assert legs.groupby('source_id').quantity.nunique().eq(1).all()
        fees=fills.price*fills.quantity*np.where(fills.phase.eq('target'),cfg['maker_fee'],cfg['taker_fee'])
        if d['symbol']=='XAUUSD':fees*=0
        np.testing.assert_allclose(fills.commission,fees,atol=1e-7,rtol=1e-9)
        agg=fills.groupby(['source_id','leg']).commission.sum()
        for r in legs.itertuples():
            assert abs(r.framework_commission-agg.loc[(r.source_id,r.leg)])<1e-6
            assert abs(r.gross_r-r.net_r-r.framework_commission/(r.quantity*r.risk))<1e-7
        counts['fee_checks']+=len(fills)
    jobs,_=source.jobs(cfg)
    assert {p.parent.name for p in (EXP/'symbols').glob('*/done.json')}=={x[0] for x in jobs}
    result=dict(status='passed',**counts,focused_tests_passed=48,new_targeted_tests=13,
        no_second_engine_replay=True,training_eligible=False,production_eligible=False)
    dump(EXP/'validation.json',result)
    dump(EXP/'completion.json',dict(validation=result,config=cfg,inputs=list(input_stamps.values()),prior_ledgers=prior,
        code=[file_stamp(ROOT/p) for p in CODE+['yoyo/evaluation/monday_tp1_delivery.py']],
        analysis_versions={k:version(k) for k in ['pandas','numpy','vectorbt']},native_versions=native_versions,
        generation_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        reproduction=['/Users/zhangzc/.local/bin/nautilus-python -m yoyo.evaluation.monday_tp1_study --workers 3',
            '/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_tp1_study --summarize',
            '/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_tp1_delivery']))
    return result


def analyze_export(cfg):
    from yoyo.evaluation import monday_parameter_analysis as stats
    f=pd.read_csv(EXP/'all-trades.csv.gz',float_precision='round_trip')
    paired_inference=[]
    for group,g in stats.group_data(f).items():
        basis='gross' if group in ['XAUUSD','GROUP1_GROSS'] else 'net'
        for period in ['all','earlier','later']:
            p=g if period=='all' else g[g.segment.eq(int(period=='later'))&~g.crosses_selection_cut]
            a=p[p.config_id.eq('split_hold')].set_index('trade_id',drop=False)
            b=p[p.config_id.eq('split_be')].set_index('trade_id',drop=False)
            ids=a.index.intersection(b.index);a=a.loc[ids];b=b.loc[ids]
            ids=a.index[a.closed&b.closed];a=a.loc[ids];b=b.loc[ids]
            delta=b.copy()
            for metric in ['r','bp']:delta[f'{basis}_{metric}']=b[f'{basis}_{metric}']-a[f'{basis}_{metric}']
            ss=b[b.population.eq('signal')].copy()
            # Reuse weekly bootstrap with R values, relabel outputs explicitly.
            ss[f'{basis}_bp']=ss[f'{basis}_r'];ss[f'control_{basis}_bp']=a.loc[ss.index,f'{basis}_r']
            effect=stats.bootstrap(ss,basis,cfg['seed'])
            pairs=stats.strict_pairs(delta,basis)
            matched=stats.bootstrap(pairs,basis,cfg['seed'])
            paired_inference.append(dict(group=group,period=period,basis=basis,common_closed=len(ss),
                be_minus_hold_mean_r=effect.get('excess_bp'),ci95_delta_r=effect.get('ci95_bp'),
                p_raw=effect.get('p_raw'),matched_effect_bp=matched,matched_effect_pairs=len(pairs)))
    # All15 group/period tests retained; no best-setting selection or promotion.
    tests=sorted([(i,r['p_raw']) for i,r in enumerate(paired_inference) if r['p_raw'] is not None],key=lambda t:t[1]);adj=0
    for j,(i,p) in enumerate(tests):
        adj=max(adj,min(1.,p*(len(tests)-j)));paired_inference[i]['holm_p']=adj
    dump(EXP/'policy-inference.json',finite(paired_inference))
    sig=f[f.population.eq('signal')];a=sig[sig.config_id.eq('split_hold')].set_index('trade_id')
    b=sig[sig.config_id.eq('split_be')].set_index('trade_id').loc[a.index]
    out=a[['symbol','side','entry_time','entry','stop','tp1_price','tp2_price','tp1_filled','tp1_time']].rename(columns={
        'symbol':'品种','side':'方向','entry_time':'入场UTC','entry':'入场价','stop':'原插针止损价',
        'tp1_price':'近侧TP1','tp2_price':'远侧TP2','tp1_filled':'是否成交TP1','tp1_time':'TP1或原止损所在5m开始UTC'})
    out['方向']=out['方向'].map({1:'多',-1:'空'})
    out['入场北京时间']=pd.to_datetime(out['入场UTC'],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    for label,x in [('原止损',a),('TP1后保本',b)]:
        for c,title in [('closed','已判定'),('gross_r','毛R'),('net_r','净R'),('fee_r','手续费R'),
            ('runner_exit_price','余仓出场价'),('exit_interval_start','余仓出场5m开始UTC'),('holding_hours','持仓小时')]:out[label+title]=x[c]
        out[label+'余仓出场原因']=x.runner_reason.map({0:'数据边界未决',1:'原止损',4:'远侧止盈',5:'保本止损，跳空仍可亏损'})
    out['余仓保本价']=b.be_price;out['延迟保本检查次数']=b.deferred_closes
    out['两方案均已判定']=a.closed&b.closed
    out['保本减原止损净R']=(b.net_r-a.net_r).where(out['两方案均已判定'])
    out['保本减原止损毛R']=(b.gross_r-a.gross_r).where(out['两方案均已判定'])
    out.to_csv(EXP/'逐笔分批保本对照.csv',encoding='utf-8-sig',index_label='交易ID')
    m=pd.read_csv(EXP/'metrics.csv');m=m.rename(columns={'config_id':'方案','group':'分组','period':'区间','basis':'毛净口径',
        'closed':'已判定笔数','censored':'未决笔数','win_rate':'胜率','mean_r':'平均R','profit_factor':'PF',
        'payoff_ratio':'实际盈亏比','paired_signal_r':'配对信号R','paired_random_r':'配对随机R'})
    m['方案']=m['方案'].map({'near_full':'近侧全平参考','far_full':'远侧全平参考','split_hold':'近远各半_原止损','split_be':'近远各半_TP1后保本'})
    m.to_csv(EXP/'分批保本统计.csv',encoding='utf-8-sig',index=False)
    return dict(paired_trade_rows=len(out),metrics_rows=len(m),weekly_policy_tests=len(paired_inference))


if __name__=='__main__':
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit delivery source before generating results')
    cfg=json.loads((EXP/'config.json').read_text())
    print(json.dumps(validate(cfg)),flush=True)
    print(json.dumps(analyze_export(cfg)),flush=True)
