"""Validate native timed fills, retain timing bounds and summarize fixed horizons.

Reads completed VectorBT outputs only. Statistical resampling clusters by NY
week across symbols. OHLC identification bounds are distinct from bootstrap
sampling intervals; neither scenario is described as an executable oracle.
"""
from __future__ import annotations
import json
import subprocess
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_limit_timed import EXP,CODE,old
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp


def finite(x):
    if isinstance(x,dict):return {k:finite(v) for k,v in x.items()}
    if isinstance(x,list):return [finite(v) for v in x]
    if isinstance(x,float) and not np.isfinite(x):return None
    return x


def validate_load(cfg):
    wide=[];cover=[];identities=[];counts=dict(symbols=0,parents=0,signals=0,native_positions=0,native_fills=0)
    expected={s for s,p in old.jobs(cfg)[0]}
    found={p.parent.name for p in (EXP/'symbols').glob('*/done.json')};assert found==expected
    for symbol in sorted(found):
        folder=EXP/'symbols'/symbol;done=json.loads((folder/'done.json').read_text());identity=json.loads((folder/'identity.json').read_text())
        assert identity['config']==cfg and identity['code']==[file_stamp(ROOT/p) for p in CODE]
        for stamp in identity['inputs']:assert file_stamp(stamp['path'])==stamp
        identities.append(dict(symbol=symbol,**identity));counts['symbols']+=1
        for k in ['parents','signals','native_positions','native_fills']:counts[k]+=done[k]
        if not done['parents']:continue
        c=pd.read_csv(folder/'coverage.csv.gz');cover.append(c[c.population.eq('signal')])
        if not (folder/'trades.csv.gz').exists():continue
        f=pd.read_csv(folder/'trades.csv.gz',float_precision='round_trip');fills=pd.read_csv(folder/'fills.csv.gz',float_precision='round_trip')
        assert not f.duplicated(['parent_id','horizon','scenario']).any()
        assert len(fills)==done['native_fills']==done['native_positions']*2
        assert fills.groupby('col').size().eq(2).all() and fills['size'].eq(1.).all()
        rates=np.where(fills.idx.eq(0),cfg['maker_fee'],cfg['taker_fee'])
        if symbol=='XAUUSD':rates*=0
        np.testing.assert_allclose(fills.fees,fills.price*rates,atol=1e-10,rtol=1e-9)
        assert f.horizon.isin(cfg['holding_minutes']).all()
        assert ((pd.to_datetime(f.exit_time,utc=True)-pd.to_datetime(f.entry_time,utc=True)).dt.total_seconds()/60).eq(f.horizon).all()
        cols=['parent_id','horizon'];g=f[f.scenario.eq('grid')].set_index(cols)
        lower=f[f.scenario.eq('lower')].set_index(cols).loc[g.index];upper=f[f.scenario.eq('upper')].set_index(cols).loc[g.index]
        assert (g[g.common_horizons].groupby(level=0).size()==len(cfg['holding_minutes'])).all()
        for m in ['net_bp','gross_bp']:
            g['lower_'+m]=lower[m];g['upper_'+m]=upper[m]
            if m=='net_bp' and symbol=='XAUUSD':assert f[m].isna().all();continue
            assert (lower[m]<=g[m]+1e-8).all() and (g[m]<=upper[m]+1e-8).all()
            np.testing.assert_allclose(lower.loc[g.opening_fill,m],upper.loc[g.opening_fill,m],atol=1e-8)
        np.testing.assert_allclose(g.gross_bp-g.fee_bp,g.net_bp,atol=1e-8,equal_nan=symbol=='XAUUSD')
        assert g.reset_index().query("population=='signal'").groupby(['week','side']).parent_id.nunique().le(1).all()
        wide.append(g.reset_index())
    f=pd.concat(wide,ignore_index=True)
    for col in ['population','symbol','week','refinement','engine_origin']:f[col]=f[col].astype('category')
    c=pd.concat(cover,ignore_index=True);c.to_csv(EXP/'entry-coverage.csv.gz',index=False)
    f.to_csv(EXP/'all-horizons.csv.gz',index=False)
    result=dict(status='passed',**counts,focused_tests=13,framework='VectorBT1.2.0',no_second_engine_replay=True)
    dump(EXP/'validation.json',result)
    dump(EXP/'completion.json',dict(validation=result,config=cfg,identities=identities,
        analysis_source=file_stamp(__file__),generation_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        reproduce=['/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_limit_timed --workers 3',
                   '/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_timed_analysis'],training_eligible=False,production_eligible=False))
    return f,c,result


def paired(x,basis):
    a=x[x.population.eq('signal')].set_index('pair_id',drop=False)
    b=x[x.population.eq('random')].copy()
    for col in ['week','symbol','side','bucket']:b=b[b[col].eq(b.pair_id.map(a[col]))]
    count=b.groupby('pair_id',observed=True).size();b=b[b.pair_id.isin(count[count.eq(3)].index)]
    m=b.groupby('pair_id',observed=True).agg(control_bp=(basis+'_bp','mean'),control_win=(basis+'_bp',lambda v:v.gt(0).mean()))
    return a.join(m,how='inner')


def bootstrap(frame,values,seed):
    if not len(frame):return {}
    w=pd.DataFrame(dict(week=frame.week.astype(str).to_numpy(),value=np.asarray(values))).groupby('week').value.agg(['sum','count'])
    a=w['sum'].to_numpy();n=w['count'].to_numpy();rng=np.random.default_rng(seed)
    ix=rng.integers(len(w),size=(2000,len(w)));sample=a[ix].sum(axis=1)/n[ix].sum(axis=1)
    null=(rng.choice([-1,1],size=(2000,len(w)))*a).sum(axis=1)/n.sum();mean=float(a.sum()/n.sum())
    return dict(mean=mean,ci95=list(map(float,np.quantile(sample,[.025,.975]))),p_positive=float((1+(null>=mean).sum())/2001),weeks=len(w))


def groups(f):
    first=f.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])
    return dict(BTC=f[f.symbol.eq('BTCUSDT')],ETH=f[f.symbol.eq('ETHUSDT')],XAUUSD=f[f.symbol.eq('XAUUSD')],
        GROUP1_GROSS=f[first],ALTCOINS=f[~first])


def summarize(f,c,cfg):
    metrics=[];inferences=[];yearly=[];directions=[];precision=[];extremes=[]
    for name,g in groups(f).items():
        basis='gross' if name in ['XAUUSD','GROUP1_GROSS'] else 'net';col=basis+'_bp'
        for common in [True,False]:
            data=g[g.common_horizons] if common else g
            for period in ['all','earlier','later']:
                part=data if period=='all' else data[data.segment.eq(int(period=='later'))&~data.crosses_selection_cut]
                for horizon,x in part.groupby('horizon',observed=True):
                    s=x[x.population.eq('signal')];p=paired(x,basis);v=s[col];lo=s['lower_'+col];hi=s['upper_'+col]
                    wins=v[v>0];loss=v[v<0]
                    row=dict(group=name,basis=basis,cohort='shared_four_horizons' if common else 'available_horizon',period=period,
                        horizon=int(horizon),n=len(s),grid_wins=len(wins),grid_losses=len(loss),grid_flat=int(v.eq(0).sum()),
                        grid_win_rate=float(v.gt(0).mean()),true_time_wins_lower=int(lo.gt(0).sum()),true_time_wins_upper=int(hi.gt(0).sum()),
                        true_time_win_rate_lower=float(lo.gt(0).mean()),true_time_win_rate_upper=float(hi.gt(0).mean()),
                        grid_mean_bp=float(v.mean()),grid_median_bp=float(v.median()),mean_bp_lower=float(lo.mean()),mean_bp_upper=float(hi.mean()),
                        mean_win_bp=float(wins.mean()),mean_loss_bp=float(loss.mean()),payoff_ratio=float(wins.mean()/-loss.mean()) if len(wins) and len(loss) else None,
                        profit_factor=float(wins.sum()/-loss.sum()) if len(loss) else None,worst_reference_bp=float(v.min()),p05_reference_bp=float(v.quantile(.05)),
                        largest_reference_bp=float(v.max()),mean_without_largest_reference_bp=float((v.sum()-v.max())/(len(v)-1)) if len(v)>1 else None,
                        largest_share_positive_bp=float(v.max()/wins.sum()) if len(wins) else None,
                        gross_reference_wins=int(s.gross_bp.gt(0).sum()),fees_erase_gross_wins=int((s.gross_bp.gt(0)&s.net_bp.le(0)).sum()) if basis=='net' else None,
                        paired=len(p),paired_signal_bp=float(p[col].mean()),paired_random_bp=float(p.control_bp.mean()),
                        paired_signal_win_rate=float(p[col].gt(0).mean()),paired_random_win_rate=float(p.control_win.mean()),
                        fine1m=int(s.entry_resolution_minutes.eq(1).sum()),exact_opening_clock=int(s.opening_fill.sum()),
                        first_entry=str(s.entry_time.min()),last_entry=str(s.entry_time.max()))
                    metrics.append(row)
                    if common:
                        inferences.append(dict(group=name,period=period,horizon=int(horizon),basis=basis,n=len(s),
                            reference_return=bootstrap(s,v,cfg['seed']),reference_vs_random=bootstrap(p,p[col]-p.control_bp,cfg['seed'])))
        for h,x in g[g.common_horizons&g.population.eq('signal')].groupby('horizon',observed=True):
            for tail,z in [('best',x.nlargest(3,col)),('worst',x.nsmallest(3,col))]:
                for row in z.to_dict('records'):extremes.append(dict(row,group=name,tail=tail,basis=basis))
            for year,a in x.groupby(pd.to_datetime(x.entry_time,utc=True).dt.year,observed=True):
                yearly.append(dict(group=name,horizon=int(h),year=int(year),basis=basis,n=len(a),grid_win_rate=float(a[col].gt(0).mean()),grid_mean_bp=float(a[col].mean()),true_win_lower=float(a['lower_'+col].gt(0).mean()),true_win_upper=float(a['upper_'+col].gt(0).mean())))
            for side,a in x.groupby('side',observed=True):
                directions.append(dict(group=name,horizon=int(h),side=int(side),basis=basis,n=len(a),grid_wins=int(a[col].gt(0).sum()),grid_win_rate=float(a[col].gt(0).mean()),grid_mean_bp=float(a[col].mean())))
            for res,a in x.groupby('entry_resolution_minutes',observed=True):
                precision.append(dict(group=name,horizon=int(h),resolution=int(res),basis=basis,n=len(a),grid_wins=int(a[col].gt(0).sum()),grid_win_rate=float(a[col].gt(0).mean()),grid_mean_bp=float(a[col].mean()),true_win_lower=float(a['lower_'+col].gt(0).mean()),true_win_upper=float(a['upper_'+col].gt(0).mean())))
    for key in ['reference_return','reference_vs_random']:
        tests=sorted([(i,v[key]['p_positive']) for i,v in enumerate(inferences) if v[key]],key=lambda t:t[1]);prev=0.
        for rank,(i,p) in enumerate(tests):prev=max(prev,min(1.,p*(len(tests)-rank)));inferences[i][key]['holm_p']=prev
    pd.DataFrame(metrics).to_csv(EXP/'metrics.csv',index=False)
    pd.DataFrame(yearly).to_csv(EXP/'yearly.csv',index=False);pd.DataFrame(directions).to_csv(EXP/'directions.csv',index=False)
    pd.DataFrame(precision).to_csv(EXP/'precision.csv',index=False);dump(EXP/'inference.json',finite(inferences))
    pd.DataFrame(extremes).to_csv(EXP/'extreme-trades.csv',index=False)
    summary=dict(config=cfg,signal_entries=len(c),shared_signals=int(c.common_horizons.fillna(False).sum()),
        common_all=[x for x in metrics if x['cohort']=='shared_four_horizons' and x['period']=='all'],
        common_later=[x for x in metrics if x['cohort']=='shared_four_horizons' and x['period']=='later'],
        entry_resolution=c.groupby('symbol',observed=True).entry_resolution_minutes.value_counts().to_dict(),
        limitations=cfg['limitations'],training_eligible=False,production_eligible=False)
    summary['entry_resolution']={str(k):int(v) for k,v in summary['entry_resolution'].items()}
    dump(EXP/'summary.json',finite(summary))
    signals=f[f.population.eq('signal')].copy()
    export=signals[['parent_id','symbol','side','horizon','common_horizons','entry_time','entry_interval_end','entry_resolution_minutes','opening_fill',
        'entry','exit_time','exit_interval_end','exit_price','gross_bp','net_bp','fee_bp','lower_gross_bp','upper_gross_bp','lower_net_bp','upper_net_bp']].rename(columns={
        'parent_id':'交易ID','symbol':'品种','side':'方向','horizon':'持仓分钟','common_horizons':'四种持仓均有数据','entry_time':'成交区间开始UTC',
        'entry_interval_end':'成交区间结束UTC','entry_resolution_minutes':'成交区间分钟','opening_fill':'开盘成交_时钟精确','entry':'成交价',
        'exit_time':'到期区间开始UTC','exit_interval_end':'到期区间结束UTC','exit_price':'格点参考平仓价','gross_bp':'参考毛收益bp',
        'net_bp':'参考净收益bp','fee_bp':'参考手续费bp','lower_gross_bp':'真实定时毛收益下界bp','upper_gross_bp':'真实定时毛收益上界bp',
        'lower_net_bp':'真实定时净收益下界bp','upper_net_bp':'真实定时净收益上界bp'})
    export['方向']=export['方向'].map({1:'多',-1:'空'})
    export['成交区间开始北京时间']=pd.to_datetime(export['成交区间开始UTC'],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    export.to_csv(EXP/'逐笔挂单定时平仓.csv',encoding='utf-8-sig',index=False)
    pd.DataFrame(metrics).rename(columns={'group':'分组','basis':'毛净口径','cohort':'样本口径','period':'时间分段','horizon':'持仓分钟','n':'笔数',
        'grid_wins':'格点参考盈利笔数','grid_win_rate':'格点参考胜率','grid_mean_bp':'格点参考平均bp','true_time_win_rate_lower':'真实定时胜率下界',
        'true_time_win_rate_upper':'真实定时胜率上界','paired_signal_bp':'配对信号bp','paired_random_bp':'配对随机bp'}).to_csv(EXP/'挂单定时胜率统计.csv',encoding='utf-8-sig',index=False)
    return dict(metrics=len(metrics),signal_horizon_rows=len(export),common_signals=summary['shared_signals'])


if __name__=='__main__':
    warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:raise RuntimeError('Commit analysis first')
    cfg=json.loads((EXP/'config.json').read_text());f,c,checks=validate_load(cfg);print(json.dumps(checks),flush=True)
    print(json.dumps(summarize(f,c,cfg)),flush=True)
