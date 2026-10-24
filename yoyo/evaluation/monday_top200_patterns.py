"""Causal candle/volume/six-MA audit and admission-only framework reuse.

Owner requested per-trade analysis of the existing 200 outcome-selected Monday
winners, then tests of a few entry ideas. This module never simulates a fill.
It preserves frozen Nautilus/VectorBT entries, exits, fees and random controls.
Only admission changes, one filter at a time; no best-exit selection is used
in validation. Features use the first touching 5m bar inside the original pin
and the last COMPLETED 5m bar before entry. Intrabar timed-entry pin features
are explicitly retrospective and cannot qualify a live entry.

Inputs: OHLCV through each feature bar; SMA/EMA20/60/120 computed on original
prefixes; volume peak/mean versus previous20 bars. No outcome, exit, MFE or
post-entry MA recovery enters admission. Descriptive excursions use only full
bars strictly after the entry bar and before the exit interval, conservatively
excluding both uncertain endpoint bars. All 200 are reviewed quantitatively;
this does not claim all images have been inspected manually.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation.monday_15m_volume_rejection import load
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT/'experiments/active/exp-monday-top200-patterns-20261024-v1'
TOP = ROOT/'artifacts/monday-top200-review-20261024'
DELIVERY = TOP/'收益率前200笔_5分钟复盘'
FAR = ROOT/'experiments/active/exp-monday-near-far-targets-vectorbt-20261024-v1'
STEP = pd.Timedelta(minutes=5)
CUT = pd.Timestamp('2023-01-01', tz='UTC')
FILTERS = ['baseline', 'volume_peak20', 'above_six', 'short_ma_retest']
LABELS = {'baseline':'原规则', 'volume_peak20':'触价5m量峰值',
          'above_six':'确认5m收回六均线', 'short_ma_retest':'确认5m回踩20双均线站稳'}


def finite(x):
    if isinstance(x, dict): return {k:finite(v) for k,v in x.items()}
    if isinstance(x, list): return [finite(v) for v in x]
    if isinstance(x, (np.integer,)): return int(x)
    if isinstance(x, (np.bool_,)): return bool(x)
    if isinstance(x, (float, np.floating)): return float(x) if np.isfinite(x) else None
    return x


def prepare(f, ma_already=False):
    f=f.copy(); f.index=f.index.as_unit('ns')
    if not ma_already:
        f['ts']=f.index.as_unit('ms').asi8; f=add_six_mas(f)
    f['vmax20']=f.volume.shift().rolling(20).max()
    f['vmean20']=f.volume.shift().rolling(20).mean()
    f['vknown20']=(f.index.to_series()-f.index.to_series().shift(20)).eq(20*STEP)
    # Price continuity is a separate gate from the numerical MA computation.
    f['ma_known']=(f.index.to_series()-f.index.to_series().shift(120)).eq(120*STEP)
    for c in SIX_MA_COLUMNS: f[c+'_delta']=f[c].diff()
    return f


def features(f, r, *, local_ma_history=False):
    """Return feature availability and primitive observations, never outcomes."""
    side=int(r['side']); t=pd.Timestamp(r['entry_time']); pin=pd.Timestamp(r['pin_time'])
    level=float(r['level']); tf=int(r['signal_minutes'])
    j=int(f.index.searchsorted(t-STEP,side='right'))-1
    if j < 1: raise ValueError('Missing complete pre-entry candle')
    b=f.iloc[j]; p=f.iloc[j-1]
    contiguous=f.index[j]+STEP==t.floor('5min')
    mas=b[list(SIX_MA_COLUMNS)].to_numpy(float)
    # Review CSVs contain correctly warmed MAs but only72 pre-pin bars. Their
    # lineage was verified during rendering; do not discard those exact MAs.
    ma_known=bool(contiguous and np.isfinite(mas).all() and
                  (local_ma_history or b.ma_known))
    favorable=side*(float(b.close)-mas)>0
    slope=np.array([b[c+'_delta'] for c in SIX_MA_COLUMNS],float)*side
    ma_edge=max(b.sma20,b.ema20) if side==1 else min(b.sma20,b.ema20)
    body_edge=min(b.open,b.close) if side==1 else max(b.open,b.close)
    wick_edge=b.low if side==1 else b.high
    retest=bool(side*(body_edge-ma_edge)>=0 and side*(wick_edge-ma_edge)<=0
                and side*(b.close-b.open)>0)
    engulf=bool(side*(b.close-b.open)>0 and side*(p.close-p.open)<0 and
                side*(b.open-p.close)<=0 and side*(b.close-p.open)>=0 and
                (b.open!=p.close or b.close!=p.open) and f.index[j]-f.index[j-1]==STEP)
    h=f.loc[(f.index>=pin)&(f.index<pin+pd.Timedelta(minutes=tf))]
    touched=h.low.le(level) if side==1 else h.high.ge(level)
    if not touched.any(): raise ValueError(f'No first5m touch in original pin: {r.get("trade_id")}')
    q=h[touched].iloc[0]; qt=h[touched].index[0]
    ran=float(q.high-q.low)
    wick=float(min(q.open,q.close)-q.low if side==1 else q.high-max(q.open,q.close))
    reclaimed=bool(side*(q.close-level)>0)
    body_inside=bool(side*((min(q.open,q.close) if side==1 else max(q.open,q.close))-level)>=0)
    pin_known=bool(qt+STEP<=t)
    vknown=bool(q.vknown20 and q.vmean20>0)
    peak=bool(vknown and q.volume>0 and q.volume>=q.vmax20)
    out=dict(feature_bar_open=str(f.index[j]),feature_available_at=str(f.index[j]+STEP),
        contiguous_pre_entry=bool(contiguous),ma_known=ma_known,
        entry_ma_favorable_count=int(favorable.sum()),entry_six_ma_reclaimed=bool(favorable.all()),
        entry_six_ma_directional_slopes=int((slope>0).sum()),
        entry_ma_width_pct=float((mas.max()-mas.min())/b.close*100),
        entry_close_ma20_pct=float(side*(b.close-ma_edge)/b.close*100),
        entry_directional_body=bool(side*(b.close-b.open)>0),entry_engulf=engulf,
        entry_short_ma_retest=retest,entry_volume_ratio=float(b.volume/b.vmean20) if b.vmean20>0 else np.nan,
        touch5m_open=str(qt),touch5m_available_at=str(qt+STEP),touch_known_at_entry=pin_known,
        touch_volume_history_known=vknown,touch_volume_peak20=peak,
        touch_volume_ratio=float(q.volume/q.vmean20) if q.vmean20>0 else np.nan,
        touch_wick_fraction=wick/ran if ran else np.nan,
        touch_body_fraction=float(abs(q.close-q.open)/ran) if ran else np.nan,
        touch_close_location=float((q.close-q.low)/ran if side==1 else (q.high-q.close)/ran) if ran else np.nan,
        touch_close_reclaimed=reclaimed,touch_body_inside=body_inside,
        touch_directional_body=bool(side*(q.close-q.open)>0),
        entry_delay_minutes=float((t-qt).total_seconds()/60))
    out['volume_peak20']=bool(pin_known and vknown and peak and contiguous)
    out['above_six']=bool(ma_known and favorable.all())
    out['short_ma_retest']=bool(ma_known and retest)
    out['baseline']=True
    return out


def observations(f,r):
    """Retrospective context: never supplied to any admission function."""
    start=pd.Timestamp(r['entry_time']); end=pd.Timestamp(r['exit_interval_start'])
    a=f[(f.index>start.floor('5min'))&(f.index+STEP<=end)]
    side=int(r['side']); entry=float(r['entry'])
    if a.empty: return dict(full_interior_bars=0)
    hi,lo=float(a.high.max()),float(a.low.min())
    mfe=max(0.,side*((hi if side==1 else lo)-entry)/entry*100)
    mae=max(0.,-side*((lo if side==1 else hi)-entry)/entry*100)
    ma=a[list(SIX_MA_COLUMNS)].to_numpy(float)
    recovered=(side*(a.close.to_numpy()[:,None]-ma)>0).all(axis=1)
    first=a.index[np.flatnonzero(recovered)[0]]+STEP if recovered.any() else None
    extreme=a.low.idxmin() if side==1 else a.high.idxmax()
    return dict(full_interior_bars=len(a),interior_mfe_pct=mfe,interior_mae_pct=mae,
                interior_adverse_extreme_time=str(extreme),
                first_post_entry_six_ma_recovery=str(first) if first is not None else None,
                recovery_delay_minutes=float((first-start).total_seconds()/60) if first is not None else None)


def narrative(r):
    known='入场前已确认' if r['touch_known_at_entry'] else '该触价柱收盘信息在入场时尚未知'
    direction='上涨' if r['side']==1 else '下跌'
    parts=[f"触价5m：内侧影线占全幅{r['touch_wick_fraction']:.1%}，实体占{r['touch_body_fraction']:.1%}；"
           f"{'收回1.618内侧' if r['touch_close_reclaimed'] else '未收回内侧'}，"
           f"{'实体未越线' if r['touch_body_inside'] else '实体跨越或在外侧'}（{known}）。",
           f"触价量/前20根均量={r['touch_volume_ratio']:.2f}倍，{'为' if r['touch_volume_peak20'] else '不是'}21根量峰。",
           f"入场前最后完整5m：收盘处于{r['entry_ma_favorable_count']}/6条均线的有利侧，"
           f"{r['entry_six_ma_directional_slopes']}/6条均线顺{direction}，"
           f"六线带宽{r['entry_ma_width_pct']:.2f}%；"
           f"{'有' if r['entry_short_ma_retest'] else '无'}20双均线影线回踩站稳，"
           f"{'有' if r['entry_engulf'] else '无'}相邻反向实体吞没。"]
    if r.get('full_interior_bars',0):
        parts.append(f"持仓内完整5m（排除入场/退出柱）最大有利波动至少{r['interior_mfe_pct']:.2f}%，"
                     f"最大不利波动至少{r['interior_mae_pct']:.2f}%；"
                     f"首次入场后六线收复：{r.get('first_post_entry_six_ma_recovery') or '退出前未观察到'}。")
    parts.append('后续走势仅供复盘，不能反填为原入场依据。')
    return ''.join(parts)


def review():
    rows=[]
    for r in pd.read_csv(TOP/'selected.csv',float_precision='round_trip').to_dict('records'):
        path=DELIVERY/'逐笔5m原始行情'/(Path(r['image_file']).stem+'.csv.gz')
        f=pd.read_csv(path,float_precision='round_trip')
        f.index=pd.DatetimeIndex(pd.to_datetime(f.pop('open_time_utc'),utc=True))
        f=prepare(f,True)
        row={**r,**features(f,r,local_ma_history=True),**observations(f,r)}
        row['review']=narrative(row);rows.append(row)
    out=pd.DataFrame(rows);out['cluster']=out.symbol+'|'+out.week+'|'+out.side.astype(str)
    out.to_csv(EXP/'top200-features.csv',index=False)
    cn=out[['rank','symbol','direction','scheme','net_pct','entry_time_bj','pin_time_bj',
            'review','image_file','cluster']].copy()
    cn.columns=['排名','币种','方向','原方案','净收益百分比','入场北京时间','触价柱北京时间',
                '逐笔K线成交量与均线分析','对应图片','行情组']
    cn.to_csv(DELIVERY/'01_逐笔K线成交量均线分析.csv',index=False,encoding='utf-8-sig')
    descriptions=[]
    for name,g in [('all200',out),('confirmed_before_entry',out[out.touch_known_at_entry]),
                   ('timed_intrabar',out[~out.touch_known_at_entry])]:
        rec=dict(group=name,n=len(g),clusters=int(g.cluster.nunique()))
        for c in ['touch_volume_peak20','touch_close_reclaimed','touch_body_inside',
                  'entry_six_ma_reclaimed','entry_short_ma_retest','entry_engulf']:
            rec[c+'_n']=int(g[c].sum())
            rec[c+'_rate']=float(g[c].mean())
            rec[c+'_equal_cluster_rate']=float(g.groupby('cluster')[c].mean().mean())
        for c in ['touch_volume_ratio','touch_wick_fraction','entry_ma_width_pct',
                  'entry_delay_minutes','interior_mae_pct','recovery_delay_minutes']:
            rec[c+'_median']=float(g[c].median())
        descriptions.append(rec)
    dump(EXP/'top200-summary.json',finite(dict(rows=200,clusters=int(out.cluster.nunique()),
        observations=descriptions,descriptive_only=True,
        actual_visual_inspection='See original delivery; these200 rows are quantitative OHLCV/MA reviews.')))
    print(json.dumps(finite(descriptions),ensure_ascii=False),flush=True)


def ledgers():
    parts=[];stamps=[]
    for path in [old.EXP/'all_trades.csv.gz',FAR/'all-trades.csv.gz']:
        stamps.append(file_stamp(path))
        f=pd.read_csv(path,float_precision='round_trip',low_memory=False)
        f=f[f.symbol.ne('XAUUSD')].copy()
        if path.parent==old.EXP:
            f['base']='engulf5m-near';f['signal_minutes']=5
            f['pin_time']=pd.to_datetime(f.entry_time,utc=True)-STEP*2
            f['engine_origin']='nautilus_cached'
        else:
            f=f[f.config_id.isin(['near-n3','far-n3'])].copy()
            f['base']='body1h3-'+f.target_mode;f['signal_minutes']=60
        for c in ['entry_time','pin_time','exit_interval_start','exit_interval_end']:
            f[c]=pd.to_datetime(f[c],utc=True,format='mixed')
        f['closed']=f.closed.astype(str).str.lower().isin(['true','1','1.0'])
        ix=pd.DatetimeIndex(f.entry_time).tz_convert('America/New_York')
        f['actual_entry_week']=(ix.tz_localize(None).normalize()-pd.to_timedelta(ix.dayofweek,unit='D')).strftime('%Y-%m-%d')
        f['cross_cut']=(f.entry_time<CUT)&(f.exit_interval_end>=CUT)
        f['uid']=f.base+'|'+f.pair_id
        parts.append(f)
    return pd.concat(parts,ignore_index=True),stamps


def process(job):
    symbol,rows=job;start=time.monotonic()
    dest=EXP/'features'/f'{symbol}.csv.gz'
    identity=json.loads((old.EXP/'symbols'/symbol/'identity.json').read_text())
    code=file_stamp(__file__)
    if dest.exists():
        receipt=json.loads(dest.with_suffix('.json').read_text())
        if receipt.get('feature_builder')!=code or receipt['inputs']!=identity['inputs']:
            raise ValueError('Stale feature cache; preserve it and use a new output version')
        return dict(symbol=symbol,cached=True,rows=len(rows))
    cfg=identity['config'];paths=[]
    for stamp in identity['inputs']:
        name=Path(stamp['path']).name
        p=(ROOT/cfg['crypto_root']/'series'/name if name.startswith('binance_um_') else ROOT/cfg['crypto_tail']/name)
        now=file_stamp(p)
        if any(now[k]!=stamp[k] for k in ['bytes','mtime_ns']): raise ValueError(f'Changed source {symbol}')
        paths.append(str(p))
    f=prepare(load(paths));out=[]
    for r in rows:
        out.append(dict(uid=r['uid'],symbol=symbol,**features(f,r)))
    pd.DataFrame(out).to_csv(dest,index=False)
    result=dict(symbol=symbol,rows=len(rows),source_rows=len(f),seconds=time.monotonic()-start,
                inputs=identity['inputs'],feature_builder=code)
    dump(dest.with_suffix('.json'),result)
    return result


def collect(workers):
    f,stamps=ledgers();s=f[f.population.eq('signal')]
    assert s.uid.is_unique
    (EXP/'features').mkdir(exist_ok=True)
    jobs=[(symbol,g.to_dict('records')) for symbol,g in s.groupby('symbol',sort=True)]
    done=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs=[pool.submit(process,j) for j in jobs]
        for fut in as_completed(futs):
            d=fut.result();done.append(d)
            if len(done)%20==0 or len(done)==len(jobs):
                print(f"Features {len(done)}/{len(jobs)} symbols, last {d['symbol']}",flush=True)
    dump(EXP/'coverage.json',dict(ledgers=stamps,symbols=done,signal_rows=len(s)))


def paired(g):
    a=g[g.population.eq('signal')&g.closed].set_index('pair_id')
    b=g[g.population.eq('random')&g.closed].copy()
    if a.empty or b.empty:return pd.DataFrame()
    for c in ['symbol','actual_entry_week','side','bucket']:
        b=b[b[c].eq(b.pair_id.map(a[c]))]
    count=b.groupby('pair_id').size();b=b[b.pair_id.isin(count[count.eq(3)].index)]
    means=b.groupby('pair_id')[['net_r','net_bp']].mean().add_prefix('control_')
    return a.join(means,how='inner')


def cluster_inference(g,p,seed=20261024):
    rng=np.random.default_rng(seed);out={}
    for label,x,col in [('absolute',g,'net_r'),('excess',p.assign(delta=p.net_r-p.control_net_r) if len(p) else p,'delta')]:
        if x.empty:continue
        a=x.groupby('actual_entry_week')[col].agg(['sum','count'])
        sums=a['sum'].to_numpy();counts=a['count'].to_numpy();means=[];null=[]
        for _ in range(2000):
            ix=rng.integers(len(a),size=len(a));means.append(sums[ix].sum()/counts[ix].sum())
            if label=='excess': null.append((sums*rng.choice([-1,1],len(a))).sum()/counts.sum())
        lo,hi=np.quantile(means,[.025,.975]);out[label+'_ci_low']=float(lo);out[label+'_ci_high']=float(hi)
        if label=='excess':out['p_raw']=(1+sum(v>=sums.sum()/counts.sum() for v in null))/2001
    return out


def metrics(g,infer=False):
    a=g[g.population.eq('signal')&g.closed];r=a.net_r;p=paired(g)
    win=r[r>0];loss=r[r<0]
    ordered=a.sort_values(['exit_interval_end','symbol','trade_id'],kind='stable').reason.eq(1).tolist()
    longest=run=0
    for v in ordered:run=run+1 if v else 0;longest=max(longest,run)
    d=dict(signals=int(g.population.eq('signal').sum()),closed=len(a),censored=int((g.population.eq('signal')&~g.closed).sum()),
           symbols=int(a.symbol.nunique()),wins=int(r.gt(0).sum()),win_rate=float(r.gt(0).mean()),
           mean_r=float(r.mean()),total_r=float(r.sum()),median_r=float(r.median()),mean_net_pct=float(a.net_bp.mean()/100),
           mean_win_r=float(win.mean()),mean_loss_r=float(loss.mean()),
           payoff=float(win.mean()/-loss.mean()),profit_factor=float(win.sum()/-loss.sum()) if len(loss) else None,
           median_planned_rr=float((a.side*(a.target_price-a.entry)/a.risk).median()),
           mean_fee_r=float((a.gross_r-a.net_r).mean()),stop_first_mean_r=float(a.stop_first_net_r.mean()),
           tp=int(a.reason.eq(4).sum()),sl=int(a.reason.eq(1).sum()),tp_net_loss=int((a.reason.eq(4)&r.le(0)).sum()),
           longest_market_stop_run=longest,paired=len(p),paired_signal_r=float(p.net_r.mean()) if len(p) else None,
           paired_random_r=float(p.control_net_r.mean()) if len(p) else None,
           paired_excess_r=float((p.net_r-p.control_net_r).mean()) if len(p) else None,
           holding_hours_median=float(((a.exit_interval_end-a.entry_time).dt.total_seconds()/3600).median()))
    if infer and len(a): d.update(cluster_inference(a,p))
    return d


def summarize():
    f,stamps=ledgers()
    z=pd.concat([pd.read_csv(p,float_precision='round_trip') for p in sorted((EXP/'features').glob('*.csv.gz'))],ignore_index=True)
    assert z.uid.is_unique
    s=f[f.population.eq('signal')].merge(z.drop(columns='symbol'),on='uid',how='left',validate='one_to_one')
    assert len(s)==len(z) and s.feature_bar_open.notna().all()
    assert (pd.to_datetime(s.feature_available_at,utc=True)<=s.entry_time).all()
    selected=s[s.volume_peak20]
    assert (pd.to_datetime(selected.touch5m_available_at,utc=True)<=selected.entry_time).all()
    s.to_csv(EXP/'all-signal-features-outcomes.csv.gz',index=False)
    rows=[];yearrows=[];feature_rows=[]
    for base,b in f.groupby('base'):
        bs=s[s.base.eq(base)]
        for key in FILTERS:
            keep=bs.loc[bs[key].fillna(False),'uid']
            eligible=b[b.uid.isin(keep)].copy()
            for group in ['ALL_CRYPTO','BTC','ETH','ALTCOINS']:
                if group=='ALL_CRYPTO':x=eligible
                elif group=='BTC':x=eligible[eligible.symbol.eq('BTCUSDT')]
                elif group=='ETH':x=eligible[eligible.symbol.eq('ETHUSDT')]
                else:x=eligible[~eligible.symbol.isin(['BTCUSDT','ETHUSDT'])]
                for period in ['all','earlier','later']:
                    g=x if period=='all' else x[(x.entry_time<CUT if period=='earlier' else x.entry_time>=CUT)&~x.cross_cut]
                    if not g.population.eq('signal').any():continue
                    rows.append(dict(base=base,filter=key,group=group,period=period,
                                     **metrics(g,infer=group in ['ALL_CRYPTO','ALTCOINS'] and period=='later')))
            for year,g in eligible.groupby(eligible.entry_time.dt.year):
                yearrows.append(dict(base=base,filter=key,year=int(year),**metrics(g)))
        for outcome,g in [('winner',bs[bs.closed&bs.net_r.gt(0)]),('loser',bs[bs.closed&bs.net_r.le(0)]),('censored',bs[~bs.closed])]:
            d=dict(base=base,outcome=outcome,n=len(g))
            for c in FILTERS[1:]+['touch_close_reclaimed','entry_engulf','entry_six_ma_reclaimed']:
                d[c+'_rate']=float(g[c].mean())
            feature_rows.append(d)
    m=pd.DataFrame(rows)
    tests=m.index[(m['filter']!='baseline')&m.group.eq('ALL_CRYPTO')&m.period.eq('later')&m.p_raw.notna()]
    prev=0.
    for rank,i in enumerate(sorted(tests,key=lambda i:m.loc[i,'p_raw'])):
        prev=max(prev,min(1.,float(m.loc[i,'p_raw'])*(len(tests)-rank)));m.loc[i,'holm_p_9']=prev
    m.to_csv(EXP/'metrics.csv',index=False);pd.DataFrame(yearrows).to_csv(EXP/'year-metrics.csv',index=False)
    pd.DataFrame(feature_rows).to_csv(EXP/'winner-loser-features.csv',index=False)
    cn=m.copy().rename(columns={'base':'基线','filter':'入场条件','group':'组别','period':'时段','signals':'机会数','closed':'已平仓',
        'censored':'未决','wins':'盈利笔数','win_rate':'胜率','mean_r':'平均净R','total_r':'累计独立净R',
        'payoff':'实际盈亏比','profit_factor':'盈利因子','paired':'完整随机配对数',
        'paired_signal_r':'配对策略净R','paired_random_r':'配对随机净R','paired_excess_r':'配对超额净R'})
    cn['入场条件']=cn['入场条件'].map(LABELS)
    cn.to_csv(DELIVERY/'02_三类入场条件框架验证.csv',index=False,encoding='utf-8-sig')
    out=dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        reused_framework_rows=len(f),signal_rows=len(s),symbols=int(s.symbol.nunique()),new_execution_count=0,
        filters=LABELS,ledger_stamps=stamps,feature_rows=len(z),no_future_feature_checks=len(s),
        all_crypto=m[(m.group=='ALL_CRYPTO')].to_dict('records'),
        later_all_crypto_tests=len(tests),production_eligible=False,training_eligible=False)
    dump(EXP/'summary.json',finite(out))
    print(m[(m.group=='ALL_CRYPTO')&m.period.isin(['all','later'])][['base','filter','period','closed','win_rate','mean_r','payoff','paired_signal_r','paired_random_r','holm_p_9']].to_string(index=False),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['review','collect','summarize']);ap.add_argument('--workers',type=int,default=3)
    args=ap.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(EXP/'config.json')]).returncode:
        raise RuntimeError('Commit builder/config before generating evidence')
    if args.stage=='review':review()
    elif args.stage=='collect':collect(args.workers)
    else:summarize()


if __name__=='__main__':main()
