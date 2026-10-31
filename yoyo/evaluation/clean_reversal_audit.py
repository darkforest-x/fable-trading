"""Audit frozen reversal labels and signal-time feature associations.

Read existing event/feature/outcome CSVs, never refit or relabel. Original
features use only signal OHLC and preceding 96 bars. Outcome audit flags use
future labels and are explicitly review-only. Figures separately render a
causal prefix and future review; prefix axis limits never see future bars.
Statistical definitions and the family of 24 comparisons are fixed in config.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.clean_reversal_labels import ROOT, EXP as PARENT, FEATURES, dump
from yoyo.evaluation.btc_1618_early_marks_charts import setup, BG, FG, MUTED, GOLD, COLORS, stamp

EXP = ROOT/'experiments/active/exp-clean-reversal-audit-20261101-v1'
OUT = ROOT/'artifacts/clean-reversal-audit-20261101'
COLS = ['feature_'+f for f in FEATURES]
FLAGS = ['half_or_fewer_advancing_blocks', 'drawdown_exceeds_terminal_progress', 'minority_time_in_favorable_direction']
FLAG_TEXT = ['四段中仅两段推进', '途中回撤大于最终推进', '多数时间未走向反转方向']


def flag_labels(d):
    """Annotate existing future outcomes without changing their labels."""
    d = d.copy()
    d[FLAGS[0]] = d.sustained_blocks_fraction.le(.5)
    d[FLAGS[1]] = d.max_close_drawdown_tr.gt(d.net_progress_tr)
    d[FLAGS[2]] = d.positive_time_fraction.lt(.5)
    d['review_flag_count'] = d[FLAGS].sum(axis=1)
    d['review_flags'] = d[FLAGS].apply(lambda r: '; '.join(k for k in FLAGS if r[k]), axis=1)
    return d


def ecdf(x, reference):
    out = np.empty_like(x, dtype=float)
    for j in range(x.shape[1]):
        a = np.sort(reference[:, j])
        out[:, j] = (np.searchsorted(a, x[:, j], 'left')+np.searchsorted(a, x[:, j], 'right'))/(2*len(a))
    return out


def matched_effect(x, y, groups, multiplicity=None):
    """Equal-stratum mean clean-minus-nonclean percentile; empty strata omitted."""
    m = np.ones(len(y)) if multiplicity is None else multiplicity
    vals=[]
    for idx in groups:
        p=m[idx]*y[idx]; n=m[idx]*(1-y[idx])
        if p.sum() and n.sum():
            vals.append((x[idx]*p[:,None]).sum(axis=0)/p.sum()-(x[idx]*n[:,None]).sum(axis=0)/n.sum())
    return np.mean(vals, axis=0) if vals else np.full(x.shape[1], np.nan)


def holm(p):
    order=np.argsort(p); out=np.ones(len(p)); running=0.
    for j,k in enumerate(order):
        running=max(running,(len(p)-j)*p[k]);out[k]=min(1.,running)
    return out


def feature_audit(all_rows):
    """Reuse original OOS split and training-only feature scaling, not model gains."""
    d=pd.read_csv(PARENT/'walkforward_predictions.csv')
    d=d[d.arm.eq('full')].sort_values(['signal_open','event_id']).drop_duplicates('episode_id').reset_index(drop=True)
    folds={r['fold']:r for r in json.loads((PARENT/'folds.json').read_text()) if r['arm']=='full'}
    indexed=all_rows.set_index('event_id'); x=np.zeros((len(d),len(COLS)))
    for fold,idx in d.groupby('fold').groups.items():
        ref=indexed.loc[folds[fold]['training_event_ids'],COLS].to_numpy()
        x[idx]=ecdf(d.loc[idx,COLS].to_numpy(),ref)
    d['stratum']=d[['symbol','side','fold','vol_bucket']].astype(str).agg('|'.join,axis=1)
    mixed=d.groupby('stratum').clean_candidate.transform('nunique').eq(2)
    coverage=dict(original_oos_events=1901,first_per_episode=len(d),mixed_events=int(mixed.sum()),unmatched_events=int((~mixed).sum()),mixed_strata=int(d.loc[mixed,'stratum'].nunique()))
    x=x[mixed];d=d[mixed].reset_index(drop=True); y=d.clean_candidate.to_numpy(float)
    groups=list(d.groupby('stratum').indices.values()); effect=matched_effect(x,y,groups)
    weeks,week_index=np.unique(d.week,return_inverse=True);rng=np.random.default_rng(20261102)
    boot=[];null=[]
    for _ in range(2000):
        counts=np.bincount(rng.integers(0,len(weeks),len(weeks)),minlength=len(weeks))
        boot.append(matched_effect(x,y,groups,counts[week_index]))
        yp=y.copy()
        for idx in groups:yp[idx]=rng.permutation(y[idx])
        null.append(matched_effect(x,yp,groups))
    boot=np.asarray(boot);null=np.asarray(null);sd=np.nanstd(boot,axis=0,ddof=1)
    safe=np.where(sd>1e-12,sd,1.); critical=np.nanquantile(np.nanmax(np.abs((boot-effect)/safe),axis=1),.95)
    p=(1+(np.abs(null)>=np.abs(effect)-1e-12).sum(axis=0))/2001
    rows=[]
    for j,feature in enumerate(COLS):
        rows.append(dict(feature=feature,matched_percentile_difference=float(effect[j]),cluster_simultaneous95_low=float(effect[j]-critical*sd[j]),cluster_simultaneous95_high=float(effect[j]+critical*sd[j]),conditional_permutation_p=float(p[j]),holm24_p=float(holm(p)[j]),null_mean=float(null[:,j].mean())))
    metrics=pd.DataFrame(rows)
    for year,idx in d.groupby(pd.to_datetime(d.signal_open,utc=True).dt.year).groups.items():
        a=d.loc[idx].copy().reset_index(drop=True);g=list(a.groupby('stratum').indices.values())
        metrics[f'effect_{year}']=matched_effect(x[idx],y[idx],g)
    d[['event_id','episode_id','stratum','week','clean_candidate']].to_csv(EXP/'feature_matched_events.csv',index=False)
    metrics.to_csv(EXP/'feature_differences.csv',index=False)
    coverage.update(matched_weeks=len(weeks),matched_clean=int(y.sum()),matched_nonclean=int(len(y)-y.sum()),simultaneous_critical=float(critical),permutations=2000,cluster_bootstraps=2000)
    return metrics,coverage


def select_review(d):
    """Choose illustrative references and near-lookalikes, never estimate efficacy."""
    gallery=d[d.in_original_gallery].copy()
    clean=gallery[gallery.clean_candidate].sort_values(['quality','event_id'],ascending=[False,True])
    refs=[clean[clean.symbol.eq('BTC')].iloc[0]]
    for _,r in clean.iterrows():
        if r.symbol not in [a.symbol for a in refs]: refs.append(r)
        if len(refs)==3:break
    initial=d[pd.to_datetime(d.week_expiry,utc=True).le(pd.Timestamp('2023-01-01',tz='UTC'))]
    refx=initial[COLS].to_numpy();pairs=[]
    for n,r in enumerate(refs,1):
        pool=gallery[~gallery.clean_candidate&gallery.symbol.eq(r.symbol)&gallery.side.eq(r.side)].copy()
        same=pool[pd.to_datetime(pool.signal_open,utc=True).dt.year.eq(pd.Timestamp(r.signal_open).year)]
        scope='same_year' if len(same) else 'all_years_fallback';pool=same if len(same) else pool
        if pool.empty:continue
        z=ecdf(pool[COLS].to_numpy(),refx);z0=ecdf(r[COLS].to_numpy(float)[None,:],refx)[0]
        pool['distance']=np.sqrt(np.mean((z-z0)**2,axis=1))
        bad=pool.sort_values(['distance','event_id']).iloc[0]
        pairs.append(dict(pair=n,reference_event=r.event_id,comparison_event=bad.event_id,matching_scope=scope,distance=float(bad.distance),comparison_group=bad.outcome_group))
    chosen=[]
    for flag in FLAGS:
        pool=clean[clean[flag]&~clean.event_id.isin(chosen)].sort_values(['quality','event_id'])
        chosen.extend(pool.head(2).event_id.tolist())
    return pairs,chosen


def candle_panel(ax,frame,e,title):
    """All price geometry and scales come from exactly the passed chart window."""
    from matplotlib.patches import Rectangle
    t=pd.Timestamp(e.signal_open).tz_convert('UTC');sx=frame.index.get_loc(t)
    low=min(frame.low.min(),e.level); high=max(frame.high.max(),e.level);span=high-low
    ax.set_title(title,loc='left',fontsize=12,color=FG,pad=10)
    ax.set_xlim(-1,len(frame));ax.set_ylim(low-.13*span,high+.13*span)
    week=pd.Timestamp(e.week);ready=(week+pd.Timedelta(days=1)).tz_localize('America/New_York');expiry=(week+pd.Timedelta(days=7)).tz_localize('America/New_York')
    valid=np.flatnonzero((frame.index>=ready)&(frame.index<expiry))
    if len(valid):ax.hlines(e.level,valid[0]-.5,valid[-1]+.5,color=GOLD,lw=1.3)
    if sx<len(frame)-1:ax.axvspan(sx+.5,len(frame)-.5,color='#3d5170',alpha=.14)
    for k,b in enumerate(frame.itertuples()):
        color=FG if k==sx else '#8c99ab';a,z=sorted([b.open,b.close])
        ax.vlines(k,b.low,b.high,color=color,lw=.8)
        ax.add_patch(Rectangle((k-.31,a),.62,max(z-a,span*.0004),facecolor=color,edgecolor=color,lw=.5))
    b=frame.loc[t];upper=e.side=='upper'
    ax.annotate('×' if upper else '✓',(sx,b.high if upper else b.low),xytext=(0,13 if upper else -15),textcoords='offset points',ha='center',color=COLORS[e.side],fontsize=23)
    ax.axvline(sx,color=COLORS[e.side],lw=.8,alpha=.5,ls='--')
    ticks=[0,sx,len(frame)-1];ticks=sorted(set(ticks));ax.set_xticks(ticks,[stamp(frame.index[i],'%m-%d\n%H:%M') for i in ticks])
    ax.tick_params(colors=MUTED,labelsize=8);ax.yaxis.tick_right();ax.grid(axis='y',color='#27313f',alpha=.4,lw=.5)
    for name,spine in ax.spines.items():spine.set_color('#27313f');spine.set_visible(name in ('bottom','right'))


def render(d,pairs,flagged):
    import matplotlib.pyplot as plt
    setup();OUT.mkdir(exist_ok=True,parents=True)
    wanted=set(flagged)|{p[k] for p in pairs for k in ['reference_event','comparison_event']};lookup=d.set_index('event_id');windows={};lineage=[]
    wanted_symbols={lookup.loc[key,'symbol'] for key in wanted}
    for receipt in json.loads((PARENT/'input_receipt.json').read_text())['sources']:
        symbol=receipt['symbol']
        if symbol not in wanted_symbols:continue
        path=Path(receipt['path']);assert path.stat().st_size==receipt['bytes'] and path.stat().st_mtime_ns==receipt['mtime_ns']
        bars=pd.read_csv(path,index_col=0,parse_dates=True,float_precision='round_trip')
        for key in sorted(wanted):
            e=lookup.loc[key]
            if e.symbol!=symbol:continue
            t=pd.Timestamp(e.signal_open).tz_convert('UTC');i=bars.index.get_loc(t)
            pre=bars.iloc[i-60:i+1].copy();review=bars.iloc[i-30:i+30].copy()
            assert len(pre)==61 and len(review)==60
            assert pre.index[-1]==t and (np.diff(pre.index.asi8)==900_000_000_000).all() and (np.diff(review.index.asi8)==900_000_000_000).all()
            assert abs(float(bars.close.iloc[i])-e.signal_close_price)<1e-7
            windows[key]=(pre,review);lineage.append(dict(event_id=key,source=str(path),prefix_first=str(pre.index[0]),prefix_last=str(pre.index[-1]),review_last=str(review.index[-1])))
    files=[]
    for pair in pairs:
        fig,axes=plt.subplots(2,2,figsize=(18,10),dpi=140);fig.subplots_adjust(left=.045,right=.94,top=.86,bottom=.09,hspace=.46,wspace=.20)
        fig.suptitle(f'对照 {pair["pair"]} · 同币同方向，按原24特征找相似样本',fontsize=20,y=.97)
        fig.text(.045,.91,'左：只看到信号收盘｜右：展开后续29根；右侧用于复盘，不进入特征。各价格轴独立。',fontsize=12,color=MUTED)
        for j,key in enumerate([pair['reference_event'],pair['comparison_event']]):
            e=lookup.loc[key];pre,review=windows[key];label='自动高分参考' if j==0 else '相似但未入候选'
            candle_panel(axes[j,0],pre,e,f'{label} · {e.symbol} {stamp(e.signal_open,"%Y-%m-%d %H:%M")} · 信号当时')
            candle_panel(axes[j,1],review,e,f'未来展开 · 事后分 {e.quality:.1f} · 推进 {e.net_progress_tr:.2f} / 回撤 {e.max_close_drawdown_tr:.2f} TR')
        fig.text(.045,.02,'金线 = 当周1.618；亮色勾叉 = 原信号；时间为北京时间开盘时间。仅示例对照，不代表成功率。',fontsize=10,color=MUTED)
        name=f'相似形态对照_{pair["pair"]:02d}.png';fig.savefig(OUT/name);plt.close(fig);files.append(name)
    fig,axes=plt.subplots(3,2,figsize=(18,14),dpi=140);fig.subplots_adjust(left=.045,right=.94,top=.90,bottom=.065,hspace=.47,wspace=.20)
    fig.suptitle('原220个候选中的边界样本 · 分数过线仍需看走势过程',fontsize=20,y=.97)
    fig.text(.045,.935,'保留原标签；这些是待核对提示，不等于已判错。阴影部分为后续29根，金线为当周1.618。',fontsize=12,color=MUTED)
    for ax,key in zip(axes.flat,flagged):
        e=lookup.loc[key];flags='；'.join(text for f,text in zip(FLAGS,FLAG_TEXT) if e[f])
        candle_panel(ax,windows[key][1],e,f'{e.symbol} {stamp(e.signal_open,"%Y-%m-%d %H:%M")}\n{flags} · 事后分 {e.quality:.1f}')
    for ax in list(axes.flat)[len(flagged):]:ax.set_visible(False)
    name=f'候选边界样本_{len(flagged):02d}.png';fig.savefig(OUT/name);plt.close(fig);files.append(name)
    return files,lineage


def run():
    d=flag_labels(pd.read_csv(PARENT/'all_labels_and_scores.csv'));c=d[d.in_original_gallery&d.clean_candidate]
    c.to_csv(EXP/'candidate_220_audit.csv',index=False)
    metrics,coverage=feature_audit(d);pairs,flagged=select_review(d);files,lineage=render(d,pairs,flagged)
    summary=dict(source_events=len(d),original_gallery=int(d.in_original_gallery.sum()),clean_candidates=len(c),flag_counts={f:int(c[f].sum()) for f in FLAGS},flagged_unique=int(c.review_flag_count.gt(0).sum()),unflagged_not_confirmed=int(c.review_flag_count.eq(0).sum()),feature_coverage=coverage,feature_rows=metrics.to_dict('records'),pairs=pairs,flagged_examples=flagged,figures=files,new_models=0,new_labels=0,training_eligible=False,production_eligible=False,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'summary.json',summary);dump(OUT/'delivery.json',dict(figures=files,lineage=lineage,selection=json.loads((EXP/'config.json').read_text())['plots'],training_eligible=False,production_eligible=False))
    pd.DataFrame(pairs).to_csv(EXP/'review_pairs.csv',index=False)
    public=c[['event_id','symbol','signal_open','side','quality','net_progress_tr','max_close_drawdown_tr','sustained_blocks_fraction','positive_time_fraction','review_flags']].copy();public['owner_confirmation']='';public.to_csv(OUT/'220候选复核表.csv',index=False,encoding='utf-8-sig')
    print(json.dumps({k:v for k,v in summary.items() if k not in ['feature_rows','flagged_examples']},ensure_ascii=False,indent=2),flush=True)
    print(metrics.sort_values('holm24_p').head(8).to_string(index=False),flush=True)


if __name__=='__main__':run()
