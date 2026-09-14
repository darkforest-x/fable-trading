"""Describe consecutive price runs above/below SMA120 and EMA120.

Owner requested OKX ETH perpetual 5m/15m/1H on 2026-09-14. This is a
retrospective segment search, not signal selection, a strategy or a return
backtest. Features use current/prior finite OHLC: close SMA120 and first-close
seeded EMA120 (alpha=2/121, adjust=False). Gaps reset both means and runs.
Only completed, UTC-aligned bars are allowed. Hourly candles require four
consecutive 15m candles. Strict comparisons: equality breaks a run. Close,
body and entire-candle definitions are reported separately, never mixed.
Future data determine a segment's final length only, not its running counter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-eth-ma120-longest-runs-20260914-v1'
MODES = ('close', 'body', 'wick')
LABELS = {'close': '收盘', 'body': '实体', 'wick': '含影线'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path, minutes, cutoff):
    """Validate ts/open/high/low/close/volume, without filling missing bars."""
    frame = pd.read_csv(path)
    frame['ts'] = pd.to_numeric(frame.ts, errors='raise').astype('int64')
    frame.index = pd.to_datetime(frame.ts, unit='ms', utc=True)
    step = pd.Timedelta(minutes=minutes)
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError('Nonmonotonic or duplicate timestamps: ' + str(path))
    if not frame.index.equals(frame.index.floor(f'{minutes}min')):
        raise ValueError('Unaligned timestamps')
    frame = frame.loc[frame.index + step <= cutoff].copy()
    if 'confirm' in frame and not frame.confirm.astype(str).eq('1').all():
        raise ValueError('Unconfirmed candles')
    fields = ['open', 'high', 'low', 'close', 'volume']
    frame[fields] = frame[fields].apply(pd.to_numeric, errors='raise')
    o, h, l, c, v = frame[fields].to_numpy(dtype=float).T
    if (not np.isfinite(frame[fields]).all().all() or (v < 0).any()
            or (np.minimum.reduce([o,h,l,c]) <= 0).any()
            or (h < np.maximum(o,c)).any() or (l > np.minimum(o,c)).any()
            or (h < l).any()):
        raise ValueError('Invalid OHLCV')
    return frame[fields]


def resample(frame, source_minutes, minutes):
    """Keep only aligned target groups with every source slot present."""
    count = minutes // source_minutes
    groups = frame.groupby(frame.index.floor(f'{minutes}min'))
    out = groups.agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    return out.loc[groups.size().eq(count)].copy()


def means(frame, minutes):
    gap = frame.index.to_series().diff().ne(pd.Timedelta(minutes=minutes))
    group = gap.cumsum().to_numpy()
    out = frame.copy()
    out['segment'] = group
    out['sma120'] = out.groupby('segment').close.transform(lambda x: x.rolling(120).mean())
    out['ema120'] = out.groupby('segment').close.transform(
        lambda x: x.ewm(alpha=2/121, adjust=False).mean())
    return out


def extract(frame, minutes, scope='full', start=None, end=None):
    """Enumerate maximal intervals; censoring flags describe sample boundaries."""
    f = frame
    if start is not None:
        f = f.loc[(f.index >= start) & (f.index + pd.Timedelta(minutes=minutes) <= end)]
    if f.empty:
        return pd.DataFrame()
    step = pd.Timedelta(minutes=minutes)
    upper = f[['sma120','ema120']].max(axis=1)
    lower = f[['sma120','ema120']].min(axis=1)
    ready = f[['sma120','ema120']].notna().all(axis=1)
    gaps = f.index.to_series().diff().ne(step).to_numpy()
    rows = []
    for mode in MODES:
        top = f.close if mode == 'close' else (f[['open','close']].min(axis=1) if mode == 'body' else f.low)
        bottom = f.close if mode == 'close' else (f[['open','close']].max(axis=1) if mode == 'body' else f.high)
        for side, mask in [('above', ready & (top > upper)), ('below', ready & (bottom < lower))]:
            m = mask.to_numpy()
            starts = np.flatnonzero(m & (gaps | ~np.r_[False, m[:-1]]))
            ends = np.flatnonzero(m & (np.r_[gaps[1:], True] | ~np.r_[m[1:], False]))
            assert len(starts) == len(ends)
            for a,b in zip(starts,ends):
                left = a == 0 or gaps[a] or not bool(ready.iloc[a-1])
                right = b == len(f)-1 or (b+1<len(f) and gaps[b+1])
                rows.append(dict(scope=scope, minutes=minutes, definition=mode, side=side,
                    bars=int(b-a+1), duration_hours=(b-a+1)*minutes/60,
                    start_utc=f.index[a].isoformat(), last_open_utc=f.index[b].isoformat(),
                    end_close_utc=(f.index[b]+step).isoformat(),
                    break_bar_open_utc=None if right else f.index[b+1].isoformat(),
                    left_censored=bool(left), right_censored=bool(right),
                    right_boundary='cache_end' if b==len(f)-1 else ('gap' if right else 'condition_failed'),
                    start_close=float(f.close.iloc[a]), end_close=float(f.close.iloc[b]),
                    price_change_pct=float((f.close.iloc[b]/f.close.iloc[a]-1)*100),
                    zero_volume_bars=int(f.volume.iloc[a:b+1].eq(0).sum()),
                    max_wick_dip_into_ma_pct=float(np.maximum(0,upper.iloc[a:b+1]-f.low.iloc[a:b+1]).max()/f.close.iloc[a]*100) if side=='above' else float(np.maximum(0,f.high.iloc[a:b+1]-lower.iloc[a:b+1]).max()/f.close.iloc[a]*100)))
    return pd.DataFrame(rows)


def stamp(value):
    return pd.Timestamp(value).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')


def draw(frame, row, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
    a = frame.index.get_loc(pd.Timestamp(row.start_utc))
    b = frame.index.get_loc(pd.Timestamp(row.last_open_utc))
    first, last = max(0,a-45), min(len(frame),b+36)
    f = frame.iloc[first:last]; x = np.arange(len(f)); cols=np.where(f.close>=f.open,'#169b86','#e0646b')
    fig, ax = plt.subplots(figsize=(15,5.3), facecolor='#f6f8fa')
    ax.set_facecolor('#ffffff')
    ax.add_collection(LineCollection([[(i,l),(i,h)] for i,l,h in zip(x,f.low,f.high)],colors=cols,linewidths=.65))
    for i,(_,c) in enumerate(f.iterrows()):
        ax.add_patch(Rectangle((i-.32,min(c.open,c.close)),.64,max(abs(c.close-c.open),1e-8),facecolor=cols[i],edgecolor=cols[i],linewidth=.6))
    ax.plot(x,f.sma120,color='#27364b',lw=1.55,label='SMA120')
    ax.plot(x,f.ema120,color='#568bde',lw=1.55,label='EMA120')
    ax.axvspan(a-first-.5,b-first+.5,color='#169b86' if row.side=='above' else '#e0646b',alpha=.10)
    ax.axvline(a-first-.5,color='#687d91',ls='--',lw=.8)
    ax.axvline(b-first+.5,color='#687d91',ls='--',lw=.8)
    ticks=np.linspace(0,len(f)-1,7).astype(int)
    ax.set_xticks(ticks,[stamp(f.index[i]).replace('2026-','26-').replace('2025-','25-').replace('2022-','22-')+'\nCST' for i in ticks],fontsize=8)
    ax.set_xlim(-1,len(f));ax.autoscale_view(scalex=False);ax.margins(y=.12)
    ax.set_title(f'OKX ETHUSDT.P | {int(row.minutes)}m | {row.definition.upper()} {row.side.upper()} | {int(row.bars)} bars / {row.duration_hours:g} hours\n{stamp(row.start_utc)} to {stamp(row.end_close_utc)} CST (end = last candle close)',loc='left',fontsize=12,pad=15)
    ax.set_ylabel('USDT');ax.grid(alpha=.15);ax.legend(loc='upper left',frameon=False)
    ax.spines[['top','right']].set_visible(False)
    fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig)


def self_test():
    idx=pd.date_range('2020-01-01',periods=10,freq='5min',tz='UTC')
    f=pd.DataFrame(dict(open=[2]*10,close=[2,2,2,1,2,2,.5,.5,.5,2],high=[3]*10,low=[.1]*10,volume=[1]*10,sma120=[1]*10,ema120=[1]*10),index=idx)
    r=extract(f,5)
    assert sorted(r.query("definition=='close' and side=='above'").bars)==[1,2,3]
    assert r.query("definition=='close' and side=='below'").iloc[0].bars==3
    assert len(r.query("definition=='wick'"))==0
    assert len(r.query("definition=='body' and side=='below'"))==0
    assert r.query("definition=='close' and side=='above'").iloc[0].left_censored
    g=f.drop(idx[1]);r=extract(g,5)
    assert r.query("definition=='close' and side=='above'").bars.max()==2
    h=pd.concat([f]*1); h=h.drop(columns=['sma120','ema120'])
    assert len(resample(h,5,15))==3
    long=pd.DataFrame({c:np.arange(300)+100 for c in ('open','high','low','close','volume')},index=pd.date_range('2020-01-01',periods=300,freq='5min',tz='UTC'))
    full=means(long,5);prefix=means(long.iloc[:200],5)
    pd.testing.assert_frame_equal(full.iloc[:200],prefix)
    assert full.sma120.first_valid_index()==long.index[119]
    assert extract(full,5).query("definition=='close' and side=='above'").iloc[0].bars==181
    print('Self-checks passed: equality, wick/body semantics, gaps, boundaries, resampling, causal prefix and SMA warmup.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--output',type=Path,default=EXPERIMENT/'results')
    args=p.parse_args()
    if args.self_test: self_test();return
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    cutoff=pd.Timestamp.now(tz='UTC')
    sources={5:ROOT/'data/kline_fetched/okx_ETH_USDT_SWAP_5m_57699.csv',15:ROOT/'data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv'}
    bars={m:load(path,m,cutoff) for m,path in sources.items()};bars[60]=resample(bars[15],15,60)
    frames={m:means(f,m) for m,f in bars.items()}
    common_start=max(f.index[0] for f in frames.values()).ceil('1h')
    common_end=min(f.index[-1]+pd.Timedelta(minutes=m) for m,f in frames.items()).floor('1h')
    runs=pd.concat([extract(f,m,s,start,end) for m,f in frames.items() for s,start,end in [('full',None,None),('common',common_start,common_end)]],ignore_index=True)
    runs=runs.sort_values(['scope','minutes','definition','side','bars','start_utc'],ascending=[True,True,True,True,False,True])
    runs['rank']=runs.groupby(['scope','minutes','definition','side']).cumcount()+1
    runs.to_csv(out/'all_runs.csv.gz',index=False)
    top=runs.loc[runs['rank'].eq(1)].copy();top.to_csv(out/'longest_runs.csv',index=False)
    runs.loc[runs['rank']<=20].to_csv(out/'top20_runs.csv',index=False)
    coverage=[]
    for m,f in frames.items():
        coverage.append(dict(minutes=m,rows=len(f),start_utc=f.index[0].isoformat(),end_close_utc=(f.index[-1]+pd.Timedelta(minutes=m)).isoformat(),gaps=int(f.index.to_series().diff().ne(pd.Timedelta(minutes=m)).sum()-1),valid_ma_bars=int(f.sma120.notna().sum())))
    charts=[]
    for _,r in top.loc[top.scope.eq('full') & top.definition.isin(['close','body'])].iterrows():
        path=out/f'{int(r.minutes)}m_{r.definition}_{r.side}.png';draw(frames[r.minutes],r,path);charts.append(str(path))
    manifest=dict(schema='ma120-runs-v1',generated_at=cutoff.isoformat(),builder_path=str(Path(__file__)),builder_sha256=sha(__file__),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),sources=[dict(path=str(path),sha256=sha(path),minutes=m) for m,path in sources.items()],coverage=coverage,common_start=common_start.isoformat(),common_end=common_end.isoformat(),holdout_exposure=1,authorization='Owner explicitly permitted any historical period; descriptive search only; not new blind validation.',outputs=[dict(path=str(path),sha256=sha(path)) for path in sorted(out.iterdir()) if path.is_file()])
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'coverage':coverage,'common':[str(common_start),str(common_end)],'longest':top.to_dict('records')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
