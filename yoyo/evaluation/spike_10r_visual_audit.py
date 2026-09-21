"""Blind, signal-close-only visual audit pack for all realized net-R > 10 cases.

Original V3 outcomes select cases only. Charts read OHLC and the six frozen
close SMA/EMA20/60/120 columns up to the original signal close, never outcomes
or subsequent bars. Matched non-winners and concealed repeats expose outcome
selection and rater consistency. This module does not label shapes or train.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from PIL import Image

from yoyo.evaluation import spike_10r_search as s
from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-10r-visual-20260921-v1')
SOURCE = Path('experiments/active/exp-spike-v9-full-backtest-20260915-v1/results/full_v1/streams')
MAS = ('s20','e20','s60','e60','s120','e120')
COLS = ('open','high','low','close',*MAS)
SEED = 921601
BINS = (.005,.01,.02,.05,.1)


def select_cases(c):
    """Outcome-stratified retrospective sampling, never a trading admission."""
    closed = c.loc[c.valid_entry & ~c.censored & np.isfinite(c.net_r)].copy()
    closed['quarter'] = closed.available_at.dt.strftime('%Y')+'Q'+closed.available_at.dt.quarter.astype(str)
    closed['bucket'] = np.searchsorted(BINS, closed.atr_fraction.to_numpy(float), side='left')
    winners = closed.loc[closed.net_r.gt(10)].sort_values('event_key').copy()
    if winners.event_key.duplicated().any():
        raise ValueError('duplicate winner')
    pool = closed.loc[closed.net_r.le(10) & np.isfinite(closed.atr_fraction)].copy()
    rng = np.random.default_rng(SEED)
    used, rows, matches = set(), [], []
    for win in winners.itertuples():
        pair = f'P{len(matches)+1:04d}'
        row = closed.loc[closed.event_key.eq(win.event_key)].iloc[0].to_dict()
        row.update(cohort='winner_gt10', pair_id=pair)
        rows.append(row)
        gap = pd.Timedelta(minutes=max(24*win.timeframe_min, 1440))
        mask = (pool.stream_key.eq(win.stream_key) & pool.quarter.eq(win.quarter)
                & pool.bucket.eq(win.bucket) & ~pool.event_key.isin(used)
                & (pool.available_at-win.available_at).abs().ge(gap))
        eligible = pool.loc[mask].sort_values('event_key') if np.isfinite(win.atr_fraction) else pool.iloc[:0]
        key = None
        if len(eligible):
            control = eligible.iloc[int(rng.integers(len(eligible)))].to_dict()
            key = control['event_key']
            used.add(key)
            control.update(cohort='matched_nonwinner',pair_id=pair)
            rows.append(control)
        matches.append(dict(pair_id=pair,winner_event_key=win.event_key,
                            control_event_key=key,eligible_count=len(eligible)))
    return pd.DataFrame(rows), pd.DataFrame(matches)


def blinded_order(cases, repeats=48):
    """Opaque shuffled IDs; duplicate identity exists only in private rows."""
    rng = np.random.default_rng(SEED+1)
    result = cases.copy()
    result['repeat'] = False
    repeat = cases.iloc[rng.choice(len(cases), size=min(repeats,len(cases)), replace=False)].copy()
    repeat['repeat'] = True
    result = pd.concat([result,repeat],ignore_index=True)
    result = result.iloc[rng.permutation(len(result))].reset_index(drop=True)
    result['visual_id'] = [f'V{i+1:04d}' for i in range(len(result))]
    result['sheet_no'] = np.arange(len(result))//8+1
    result['sheet_position'] = np.arange(len(result))%8+1
    return result


def causal_view(frame, available_at, minutes):
    """Read at most 128 complete native bars ending exactly at the decision."""
    cutoff = pd.Timestamp(available_at)
    close_times = frame.index+pd.Timedelta(minutes=int(minutes))
    right = close_times.searchsorted(cutoff, side='right')-1
    if right < 0 or close_times[right] != cutoff:
        raise ValueError('decision does not identify a complete native bar')
    view = frame.iloc[max(0,right-127):right+1].loc[:,COLS].copy()
    if len(view)<32 or not np.isfinite(view.to_numpy(float)).all():
        return view, 'unknown_warmup'
    if len(view)>1 and (view.index.to_series().diff().iloc[1:]!=pd.Timedelta(minutes=int(minutes))).any():
        return view, 'unknown_gap'
    return view, 'ready'


def render_card(view, visual_id, minutes, status, path):
    """Only physically truncated price/MA data and an opaque ID are inputs."""
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.collections import LineCollection, PolyCollection
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8})
    fig, axes = plt.subplots(2,1,figsize=(7.2,4.4),dpi=100,
                             gridspec_kw={'height_ratios':[1,1.65]})
    fig.patch.set_facecolor('white')
    fig.suptitle(f'{visual_id}  |  {minutes}m  |  signal close = bar 0',fontsize=12,fontweight='bold',y=.985)
    if status != 'ready':
        for ax in axes: ax.text(.5,.5,status,ha='center',va='center',transform=ax.transAxes)
    else:
        normalized = view*100/float(view.close.iloc[-1])
        for ax, part in zip(axes,[normalized,normalized.iloc[-32:]]):
            x = np.arange(len(part))-len(part)+1
            o,h,l,c = (part[n].to_numpy() for n in ('open','high','low','close'))
            colors = np.where(c>=o,'#15936c','#d65363')
            ax.add_collection(LineCollection([[(i,low),(i,hi)] for i,low,hi in zip(x,l,h)],colors=colors,linewidths=.65))
            eps=max(float(np.ptp(h))*0.0008,1e-7)
            verts=[[(i-.31,op),(i+.31,op),(i+.31,cl if abs(cl-op)>eps else op+eps),(i-.31,cl if abs(cl-op)>eps else op+eps)] for i,op,cl in zip(x,o,c)]
            ax.add_collection(PolyCollection(verts,facecolors=colors,edgecolors=colors,linewidths=.35))
            for period,color in ((20,'#ed8a23'),(60,'#198ed0'),(120,'#963eb9')):
                for kind,style in (('s','-'),('e','--')):
                    ax.plot(x,part[f'{kind}{period}'],color=color,linestyle=style,lw=1.25,alpha=.92)
            values=part.to_numpy(float)
            lo,hi=float(values.min()),float(values.max());pad=max((hi-lo)*.065,.02)
            ax.set_ylim(lo-pad,hi+pad);ax.set_xlim(float(x[0])-1,1)
            ax.axvline(0,color='#414954',lw=.7,alpha=.65)
            ax.grid(alpha=.15);ax.tick_params(labelsize=7)
            ax.set_title(f'{len(part)} closed bars',loc='left',fontsize=8,pad=2)
        axes[1].set_xlabel('bar offset from original signal')
    fig.text(.5,.015,'Orange 20 | Blue 60 | Purple 120      SMA solid / EMA dashed      No future bars',ha='center',fontsize=8)
    fig.subplots_adjust(top=.91,bottom=.12,left=.08,right=.985,hspace=.29)
    fig.savefig(path,dpi=100)
    plt.close(fig)


def render_stream(args):
    dataset, output, key, rows = args
    dr = json.loads((dataset/'streams'/key/'completion.json').read_text())
    source = SOURCE/key
    if s.digest(source/'completion.json') != dr['source_completion_sha256']:
        raise ValueError('source completion drift')
    sr=json.loads((source/'completion.json').read_text())
    raw=base.SOURCE_STREAMS/key
    if s.digest(raw/'completion.json')!=sr['raw_completion_sha256'] or s.digest(raw/'control_cache.pkl.gz')!=sr['cache_sha256']:
        raise ValueError('raw source identity drift')
    context=base.load_verified_stream(raw)
    frame=context.cache['bars']
    result=[]
    for row in rows:
        view,status=causal_view(frame,row['available_at'],row['timeframe_min'])
        filename=f"blind/cards/{row['visual_id']}.png"
        render_card(view,row['visual_id'],row['timeframe_min'],status,output/filename)
        result.append(dict(visual_id=row['visual_id'],image_path=filename,sheet_no=row['sheet_no'],
            sheet_position=row['sheet_position'],timeframe_min=row['timeframe_min'],status=status,
            visible_end_at=str(view.index[-1]+pd.Timedelta(minutes=row['timeframe_min'])),
            causal=bool(view.index[-1]+pd.Timedelta(minutes=row['timeframe_min'])==row['available_at']),
            sha256=s.digest(output/filename)))
    if s.digest(raw/'control_cache.pkl.gz')!=sr['cache_sha256']:
        raise ValueError('raw source changed during rendering')
    return result,dict(stream_key=key,raw_cache_sha256=sr['cache_sha256'],source_completion_sha256=dr['source_completion_sha256'])


def build(dataset, output, workers=4):
    deps=[Path(__file__),Path('tests/evaluation/test_spike_10r_visual_audit.py'),EXP/'config.json',EXP/'PROJECT_PLAN.md']
    if not _committed(deps):raise ValueError('commit builder/test/config/plan before generating charts')
    if output.exists():raise ValueError('refuse to overwrite visual pack')
    cfg=json.loads((EXP/'config.json').read_text())
    if cfg['dataset_receipt_sha256']!=s.digest(dataset/'receipt.json') or cfg['seed']!=SEED:
        raise ValueError('dataset/config identity mismatch')
    c,_,_=s.load_candidates(dataset)
    cases,matches=select_cases(c)
    assert cases.cohort.eq('winner_gt10').sum()==448
    # Related episodes are reporting units only; retain every original event.
    episodes={}
    for asset,g in cases.sort_values('available_at').groupby('asset'):
        cluster=0;last=None
        for row in g.itertuples():
            if last is None or row.available_at-last>pd.Timedelta(hours=24):cluster+=1
            episodes[row.event_key]=f'{asset}:{cluster}'
            last=row.available_at
    cases['episode_id']=cases.event_key.map(episodes)
    ordered=blinded_order(cases,repeats=cfg['blind_repeats'])
    (output/'blind/cards').mkdir(parents=True)
    (output/'blind/sheets').mkdir()
    ordered.to_csv(output/'private.csv',index=False)
    matches.to_csv(output/'private_matches.csv',index=False)
    tasks=[(dataset,output,key,g.to_dict('records')) for key,g in ordered.groupby('stream_key')]
    manifests,sources=[],[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i,(records,source) in enumerate(pool.map(render_stream,tasks)):
            manifests.extend(records);sources.append(source)
            if (i+1)%50==0:print(f'rendered {i+1}/{len(tasks)} streams',flush=True)
    manifest=pd.DataFrame(manifests).sort_values('visual_id')
    assert manifest.causal.all() and len(manifest)==len(ordered)
    manifest.to_csv(output/'private_clock_audit.csv',index=False)
    public=manifest[['visual_id','image_path','sheet_no','sheet_position','timeframe_min','status']]
    public.to_csv(output/'blind/reviewer_manifest.csv',index=False)
    for number, group in public.groupby('sheet_no'):
        sheet=Image.new('RGB',(1440,1760),'white')
        for row in group.itertuples():
            pos=int(row.sheet_position)-1
            with Image.open(output/row.image_path) as card:sheet.paste(card,((pos%2)*720,(pos//2)*440))
        sheet.save(output/f'blind/sheets/sheet_{number:03d}.png')
    files={str(p.relative_to(output)):s.digest(p) for p in output.rglob('*') if p.is_file()}
    s.dump(output/'receipt.json',dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        dataset_receipt_sha256=s.digest(dataset/'receipt.json'),dependencies={str(p):s.digest(p) for p in deps},
        winners=448,controls=int(cases.cohort.eq('matched_nonwinner').sum()),unmatched=int(matches.control_event_key.isna().sum()),
        exposures=len(ordered),repeats=int(ordered.repeat.sum()),sources=sources,files=files,
        six_ma_columns=MAS,all_causal=True,status_counts=manifest.status.value_counts().to_dict(),
        production_eligible=False,training_eligible=False))
    print(json.dumps(dict(winners=448,controls=len(cases)-448,exposures=len(ordered),sheets=int(public.sheet_no.max()),statuses=manifest.status.value_counts().to_dict())))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    build(args.dataset,args.output,args.workers)
