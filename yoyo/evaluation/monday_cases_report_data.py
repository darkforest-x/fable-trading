"""Build reviewed report data from frozen Monday1618 Nautilus ledgers.

No simulation or parameter search. Case1 spot rows are replaced by the verified
New York clock correction, never mixed with obsolete fixed-EST rows. Returns
use closed trades; censored trades stay in the inspectable ledger. Sampling is
one seeded draw per case x BTC/ETH/XAU/ALT x side x outcome, without chart-aesthetic
or duration filtering. Candle data are read from the existing source files.
Entry/exit dates used in descriptive cuts are not new trading filters.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_5m_wick_gold as quality
from yoyo.evaluation import monday_5m_wick_gold_clock as gold
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation.range_framework import ROOT, file_stamp, dump

OUT=ROOT/'artifacts/monday-cases-report-20261023'
NAMES={1:'exp-monday-5m-wick-engulf-20261023-v1',2:'exp-monday-15m-volume-rejection-20261023-v1',3:'exp-monday-1h-wick-hold-20261023-v1',4:'exp-monday-4h-close-hold-20261023-v1'}
TF={1:5,2:15,3:60,4:240}
DELAY={1:10,2:60,3:240,4:960}
SEED=20261023
GROUPS=['BTC','ETH','XAUUSD','GROUP1','ALTCOINS']


def records(f):
    return json.loads(f.to_json(orient='records',date_format='iso',double_precision=12))


def prepare(case):
    p=ROOT/'experiments/active'/NAMES[case]
    f=pd.read_csv(p/'all_trades.csv.gz')
    sources=[p/'all_trades.csv.gz',p/'summary.json',p/'config.json']
    j=json.loads((p/'summary.json').read_text())
    if case==1:
        q=gold.EXP/'gold-trades.csv.gz';f=pd.concat([f[f.symbol.ne('XAUUSD')],pd.read_csv(q)],ignore_index=True)
        corrected=json.loads((gold.EXP/'summary.json').read_text());j['tables'].update(corrected['tables']);j['comparisons'].update(corrected['comparisons']);sources.extend([q,gold.EXP/'summary.json'])
    f['case']=case
    f['asset']=f.symbol.map({'BTCUSDT':'BTC','ETHUSDT':'ETH','XAUUSD':'XAUUSD'}).fillna('ALTCOINS')
    f['entry_dt']=pd.to_datetime(f.entry_time,utc=True)
    f['exit_dt']=pd.to_datetime(f.exit_interval_end,utc=True)
    f['pin_dt']=f.entry_dt-pd.Timedelta(minutes=DELAY[case])
    if 'pin_time' in f:
        known=f.pin_time.notna();assert pd.to_datetime(f.loc[known,'pin_time'],utc=True).eq(f.loc[known,'pin_dt']).all()
    f['holding_h']=(f.exit_dt-f.entry_dt).dt.total_seconds()/3600
    f['risk_pct']=f.risk/f.entry*100
    f['planned_rr']=f.side*(f.target_price-f.entry)/f.risk
    f['direction']=np.where(f.side.eq(1),'多','空')
    f['basis_r']=f.net_r.where(f.symbol.ne('XAUUSD'),f.gross_r)
    f['basis_bp']=f.net_bp.where(f.symbol.ne('XAUUSD'),f.gross_bp)
    f['outcome']=np.where(~f.closed,'截尾',np.where(f.basis_r.gt(0),'盈利',np.where(f.basis_r.lt(0),'亏损','持平')))
    bjt=f.entry_dt.dt.tz_convert('Asia/Shanghai');f['year']=bjt.dt.year;f['month']=bjt.dt.strftime('%Y-%m');f['hour']=bjt.dt.hour;f['weekday']=bjt.dt.dayofweek+1
    f['exit_month']=f.exit_dt.dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m')
    return f,j,sources


def subset(f,group):
    return f[f.asset.ne('ALTCOINS')] if group=='GROUP1' else f[f.asset.eq(group)]


def stats(g,gross=False):
    c=g[g.closed].copy();v=c.gross_r if gross else c.basis_r;bp=c.gross_bp if gross else c.basis_bp
    n=len(c);result=dict(total=len(g),n=n,censored=int((~g.closed).sum()),gross_basis=bool(gross or (len(g)>0 and g.symbol.eq('XAUUSD').all())))
    if not n:return result
    wins=v[v>0];loss=v[v<0];curve=v.iloc[np.argsort(c.exit_dt.to_numpy(),kind='stable')].cumsum();peak=curve.cummax().clip(lower=0);maxdd=(peak-curve).max()
    result.update(win_pct=float(v.gt(0).mean()*100),tp_pct=float(c.reason.eq(4).mean()*100),sl_pct=float(c.reason.eq(1).mean()*100),mean_r=float(v.mean()),median_r=float(v.median()),sum_r=float(v.sum()),mean_bp=float(bp.mean()),gross_mean_r=float(c.gross_r.mean()),mean_fee_r=None if result['gross_basis'] else float(c.fee_r.mean()),mean_win_r=float(wins.mean()) if len(wins) else None,mean_loss_r=float(loss.mean()) if len(loss) else None,pf=float(wins.sum()/-loss.sum()) if len(loss) else None,win_n=len(wins),loss_n=len(loss),flat_n=int(v.eq(0).sum()),p05_r=float(v.quantile(.05)),p95_r=float(v.quantile(.95)),min_r=float(v.min()),max_r=float(v.max()),holding_median_h=float(c.holding_h.median()),holding_p90_h=float(c.holding_h.quantile(.9)),risk_median_pct=float(c.risk_pct.median()),planned_rr_median=float(c.planned_rr.median()),ledger_max_drawdown_r=float(maxdd),fee_erased_wins=int((c.gross_r.gt(0)&v.le(0)).sum()),ambiguity=int(c.ambiguous_bar.sum()),first_entry=str(c.entry_dt.min()),last_entry=str(c.entry_dt.max()))
    return result


def cluster_ci(c,metric):
    a=c.groupby('week')[metric].agg(['sum','count']);rng=np.random.default_rng(SEED);v=a['sum'].to_numpy();n=a['count'].to_numpy()
    if len(a)<2:return [None,None]
    ix=rng.integers(len(a),size=(2000,len(a)));bs=v[ix].sum(axis=1)/n[ix].sum(axis=1)
    return list(map(float,np.quantile(bs,[.025,.975])))


def make_stats(frames,summaries):
    aggregate=[];cuts=[];monthly=[];dist=[];symbols=[];controls=[]
    bins=[-np.inf,-2,-1.2,-1,-.5,0,.5,1,2,3,5,10,np.inf]
    for case,f in frames.items():
        s=f[f.population.eq('signal')]
        for group in GROUPS:
            g=subset(s,group);gross=group=='XAUUSD' or (group=='GROUP1' and case!=2);c=g[g.closed]
            if not len(g):continue
            row=dict(case=case,group=group,**stats(g,gross));metric='gross_r' if gross else 'basis_r';row['ci_low'],row['ci_high']=cluster_ci(c,metric)
            random=subset(f[f.population.eq('random')],group);rs=stats(random,gross);row['random_mean_r']=rs.get('mean_r');row['random_n']=rs['n'];aggregate.append(row)
            key={'GROUP1':('BTC_ETH_GOLD' if case==1 else 'GROUP1_BTC_ETH_ONLY' if case==2 else 'GROUP1_BTC_ETH_GOLD')}.get(group,group)
            ref=summaries[case]['tables'].get(key,{}).get('all',{}).get('signal',{})
            if ref:
                assert ref['n']==row['n'],(case,group,ref['n'],row['n'])
                expected=ref['mean_gross_r'] if gross else ref['mean_net_r']
                assert abs(row['mean_r']-expected)<1e-9,(case,group,row['mean_r'],expected)
            comparison=summaries[case]['comparisons'].get(key)
            if comparison:controls.append(dict(case=case,group=group,**comparison))
            for dimension in ['year','direction','hour','weekday','segment']:
                for value,x in g.groupby(dimension):
                    # Keep controls attached to the selected signal cohort, rather
                    # than reselecting controls by their unrelated entry hour.
                    r=random[random.pair_id.isin(x.loc[x.closed,'pair_id'])]
                    rs=stats(r,gross)
                    cuts.append(dict(case=case,group=group,dimension=dimension,value=str(value),random_mean_r=rs.get('mean_r'),random_n=rs['n'],**stats(x,gross)))
            if len(c):
                v=c.gross_r if gross else c.basis_r
                hist=pd.cut(v,bins=bins,right=False).value_counts(sort=False)
                for idx,(label,count) in enumerate(hist.items()):dist.append(dict(case=case,group=group,bin=str(label),order=idx,n=int(count),pct=float(count/len(c)*100)))
                sums=c.assign(value=v).groupby('exit_month').agg(net_r=('value','sum'),n=('value','size'));sums['cumulative_r']=sums.net_r.cumsum()
                for month,r in sums.iterrows():monthly.append(dict(case=case,group=group,month=month,net_r=float(r.net_r),cumulative_r=float(r.cumulative_r),n=int(r.n)))
        for symbol,g in s.groupby('symbol'):
            gross=g.symbol.eq('XAUUSD').all();rs=stats(f[f.symbol.eq(symbol)&f.population.eq('random')],gross)
            symbols.append(dict(case=case,symbol=symbol,asset=g.asset.iloc[0],random_mean_r=rs.get('mean_r'),random_n=rs['n'],**stats(g,gross)))
    return dict(aggregate=aggregate,cuts=cuts,monthly=monthly,distribution=dist,symbols=symbols,controls=controls)


def pick_samples(frames):
    rng=np.random.default_rng(SEED);chosen=[];coverage=[]
    for case,f in frames.items():
        s=f[f.population.eq('signal')&f.closed].sort_values(['symbol','entry_time','trade_id'])
        for asset in ['BTC','ETH','XAUUSD','ALTCOINS']:
            for side in [1,-1]:
                for outcome in ['盈利','亏损']:
                    pool=s[s.asset.eq(asset)&s.side.eq(side)&s.outcome.eq(outcome)]
                    coverage.append(dict(case=case,asset=asset,side=side,outcome=outcome,population=len(pool)))
                    if not len(pool):continue
                    r=pool.iloc[int(rng.integers(len(pool)))].to_dict();r['sample_id']=f'sample-{len(chosen)+1:02d}';r['sampling_pool']=len(pool);chosen.append(r)
    return chosen,coverage


def compact_bars(f):
    rows=[]
    for t,r in f.iterrows():
        rows.append([int(t.timestamp()*1000),*[float(r[x]) for x in old.OHLC],float(r.volume) if 'volume' in r and np.isfinite(r.volume) else None])
    return rows


def sample_charts(selected):
    cfg=json.loads((old.EXP/'config.json').read_text());jobs=dict(old.jobs(cfg)[0]);output=[]
    for symbol in sorted({r['symbol'] for r in selected}):
        paths=jobs[symbol]
        if symbol=='XAUUSD':
            raw,_=gold.read_minutes(paths);f,_=quality.clean_minutes(raw)
        else:f=volume.load(paths)
        for r in [x for x in selected if x['symbol']==symbol]:
            case=int(r['case']);tf=TF[case];step=pd.Timedelta(minutes=tf);pin=r['pin_dt'];entry=r['entry_dt'];exit=r['exit_dt'];original=f
            if symbol=='XAUUSD':
                seg=int(str(r['trade_id']).split('-')[0][1:]);original=f[f.quality_segment.eq(seg)]
            start=pin-step*24;end=exit+step*8
            window=original.loc[start:end]
            grouped=window.resample(f'{tf}min',closed='left',label='left');agg={x:('first' if x=='open' else 'last' if x=='close' else 'max' if x=='high' else 'min') for x in old.OHLC}
            if 'volume' in window:agg['volume']='sum'
            h=grouped.agg(agg).dropna(subset=old.OHLC)
            e=original.loc[entry];p=h.loc[pin]
            assert abs(e.open-r['entry'])<=max(1e-7,r['entry']*1e-7),(symbol,'entry')
            assert abs((p.low if r['side']==1 else p.high)-r['stop'])<=max(1e-7,r['stop']*1e-7),(symbol,'pin stop')
            # The entry panel never changes timeframe, irrespective of trade duration.
            detail=h.loc[pin-step*24:entry+step*24]
            # Full path is kept at its native signal period. A dense path is scrollable in the UI.
            full=h
            checks=[]
            for k in range(1,2 if case==1 else 4):
                t=pin+step*k;b=h.loc[t];level=r['level'];bodymin=min(b.open,b.close);bodymax=max(b.open,b.close)
                rule=(bodymin>level if r['side']==1 else bodymax<level) if case==2 else ((b.low>level if r['side']==1 else b.high<level) if case==3 else (b.close>=level if r['side']==1 else b.close<=level))
                if case==1:rule=(b.open<=p.close and b.close>=p.open and b.close>b.open) if r['side']==1 else (b.open>=p.close and b.close<=p.open and b.close<b.open)
                assert rule,(r['trade_id'],case,k)
                checks.append(dict(number=k,open_time=str(t),close_time=str(t+step),open=float(b.open),high=float(b.high),low=float(b.low),close=float(b.close),passed=bool(rule)))
            output.append(dict(sample_id=r['sample_id'],case=case,asset=r['asset'],symbol=symbol,side=int(r['side']),direction=r['direction'],outcome=r['outcome'],trade_id=r['trade_id'],sampling_pool=r['sampling_pool'],timeframe_minutes=tf,pin_time=str(pin),entry_time=str(entry),exit_interval_start=r['exit_interval_start'],exit_interval_end=r['exit_interval_end'],framework_exit_time=r['framework_exit_time'],entry=r['entry'],stop=r['stop'],target=r['target_price'],exit_price=r['exit_price'],level=r['level'],monday_high=r['monday_high'],monday_low=r['monday_low'],gross_r=r['gross_r'],net_r=r['net_r'] if symbol!='XAUUSD' else None,fee_r=r['fee_r'] if symbol!='XAUUSD' else None,risk_pct=r['risk_pct'],planned_rr=r['planned_rr'],holding_h=r['holding_h'],ambiguous=bool(r['ambiguous_bar']),pin_volume=r.get('pin_volume') if case==2 else None,prior20_volume_max=r.get('prior20_volume_max') if case==2 else None,confirmations=checks,detail_bars=compact_bars(detail),full_bars=compact_bars(full),source_files=[str(Path(x).relative_to(ROOT)) if str(x).startswith(str(ROOT)) else str(x) for x in paths]))
        print(f'render data {symbol}',flush=True)
    return sorted(output,key=lambda x:x['sample_id'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stats-only',action='store_true');parser.add_argument('--reuse-samples',action='store_true');args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_cases_report_data.py'],cwd=ROOT).returncode:raise RuntimeError('Commit report builder before generating')
    OUT.mkdir(parents=True,exist_ok=True);frames={};summaries={};paths=[]
    for case in NAMES:frames[case],summaries[case],sources=prepare(case);paths.extend(sources)
    tables=make_stats(frames,summaries);selected,coverage=pick_samples(frames)
    stamp=[file_stamp(x) for x in paths]
    if args.reuse_samples:assert json.loads((OUT/'source-stamps.json').read_text())==stamp,'Changed report inputs'
    dump(OUT/'source-stamps.json',stamp);dump(OUT/'sample-selection.json',coverage)
    queries={}
    for name,rows in tables.items():queries[name]={'rows':rows,'source':{'label':'Frozen Nautilus ledgers / corrected spot clock','files':[str(x.relative_to(ROOT)) for x in paths], 'evidenceFlow':[{'title':'读取冻结结果','detail':'Case1 crypto unchanged; replace all old XAU rows with gold-trades.csv.gz from NY clock v2.'},{'title':'分组与统计','detail':'Only closed trades determine returns and win rates. Censored stay separate. GROUP1 gross basis; other crypto net, gold gross. Descriptive calendar cuts use Asia/Shanghai entry time; cumulative ledger uses exit month.'}],'metricDefinitions':[{'label':'R','definition':'side × (exit − entry) / initial stop distance; net subtracts actual two-fill fees. GROUP1 and XAU gross only. Independent trades, not a funded portfolio.'}],'caveats':['Same-week random controls inherit market trend. Parameter search and new simulations were not performed.']}}
    signals=pd.concat([x[x.population.eq('signal')] for x in frames.values()],ignore_index=True)
    signals['pin_time']=signals.pin_dt.astype(str)
    cols=['case','asset','symbol','trade_id','direction','pin_time','entry_time','exit_interval_start','exit_interval_end','entry','stop','target_price','exit_price','level','monday_high','monday_low','gross_r','net_r','fee_r','outcome','closed','risk_pct','planned_rr','holding_h']
    queries['trades']={'rows':records(signals[cols]),'source':{'label':'Complete signal trade ledger','files':[str(x.relative_to(ROOT)) for x in paths if x.suffix=='.gz'],'metricDefinitions':[{'label':'退出时间','definition':'exit_interval_start..end is the observed5m interval; exact sub-bar fill second is unknown. Censored are not wins or losses.'}]}}
    if args.reuse_samples:
        samples=json.loads((OUT/'samples.json').read_text())
        assert {(r['sample_id'],r['trade_id']) for r in samples}=={(r['sample_id'],r['trade_id']) for r in selected}
    elif not args.stats_only:
        samples=sample_charts(selected);dump(OUT/'samples.json',samples)
    else:samples=[]
    queries['samples']={'rows':[{**{k:v for k,v in x.items() if k not in ['detail_bars','full_bars','confirmations']},'detail_json':json.dumps(x['detail_bars'],separators=(',',':')),'full_json':json.dumps(x['full_bars'],separators=(',',':')),'checks_json':json.dumps(x['confirmations'],separators=(',',':'))} for x in samples], 'payloadColumns':['detail_json','full_json','checks_json'], 'source':{'label':'Seeded outcome/side/asset-stratified historical candle samples','files':[str(x.relative_to(ROOT)) for x in paths],'evidenceFlow':[{'title':'抽样','detail':'Seed20261023, one uniform draw per case × BTC/ETH/XAU/ALT × long/short × win/loss; no duration or visual filtering. Missing cells remain missing. Charts explain trades, not overall prevalence.'},{'title':'重绘','detail':'Source5m OHLCV resampled onto original UTC signal timeframe. Spot comes from NY-clock M1 with existing conflict exclusion; spot volume unavailable. Full trade path retained, detail fixed around entry.'}]}}
    snapshot=dict(surface='report',title='周一1.618：四种反转确认的回测证据',generatedAt=pd.Timestamp.now(tz='UTC').isoformat(),status='reviewed',buildStatus='creating',filters=[],queries=queries,report={})
    dump(OUT/'reviewed.json',snapshot)
    signals[cols].to_csv(OUT/'all-signal-trades.csv.gz',index=False)
    dump(OUT/'analysis-receipt.json',dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),signal_rows=len(signals),sample_count=len(samples),sampling_seed=SEED,case1_gold_rows=int(len(frames[1][frames[1].symbol.eq('XAUUSD')&frames[1].population.eq('signal')])),no_engine_replay=True,source_stamps_verified=True))
    for before,x in zip(stamp,paths):assert file_stamp(x)==before
    print(json.dumps({'signals':len(signals),'samples':len(samples),'aggregate_rows':len(tables['aggregate'])}),flush=True)

if __name__=='__main__':main()
