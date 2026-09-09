"""Aggregate frozen multivenue events, paired references and causal accounts.

Source contract: exp-altseason-multivenue-20260910-v1/PROJECT_PLAN.md. This
module reads the dataset builder's fixed outputs; it never changes signal
thresholds, fetches private data, trains or touches a live service. Daily market
breadth joins are backward-as-of completed UTC days, not future market labels.
Calendar appreciation labels are retrospective opportunity diagnostics only.
Permutation units are asset means of asset/week paired excesses, so duplicated
venues and repeated bars cannot masquerade as independent price paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.altseason_engine import ARMS
from yoyo.evaluation.altseason_portfolio import run_portfolio, summarize_portfolio

ROOT=Path(__file__).resolve().parents[2]
EXPERIMENT=ROOT/'experiments/active/exp-altseason-multivenue-20260910-v1'
START=pd.Timestamp('2026-07-10T00:00Z')
SPLIT=pd.Timestamp('2026-08-10T00:00Z')
END=pd.Timestamp('2026-09-09T00:00Z')
PERIODS={'full':(START,END),'first31':(START,SPLIT),'last30':(SPLIT,END)}
SCOPES=('okx','binance','gate','combined')
SCORES=('relative_volume','tr_expansion','momentum10','near_zero_bars','prior24h_return','six_ma_width_atr')


def read_events(path):
    f=pd.read_csv(path)
    for col in ('decision_time','entry_time','exit_time','exit_time_lower','exit_time_upper','window_start','window_end','peak_time','time'):
        if col in f:f[col]=pd.to_datetime(f[col],utc=True)
    return f


def scope_rows(f,scope):
    if scope!='combined':return f.loc[f.venue.eq(scope)].copy()
    # Exact-base identity only; unverified multiplier aliases are excluded
    # here, but remain in their venue's own research and coverage tables.
    return f.loc[~f.asset.astype(str).str.match(r'^(?:1000|10000|1000000)[A-Za-z]')].copy()


def _week(times):
    day=pd.DatetimeIndex(times).normalize()
    return day-pd.to_timedelta(day.weekday,unit='d')


def paired_test(group):
    g=group.loc[np.isfinite(group.excess_bp)].copy()
    if g.empty:return np.nan,0,np.nan
    g['week']=_week(g.decision_time)
    units=g.groupby(['asset','week'],observed=True).excess_bp.mean().groupby(level=0).mean().to_numpy()
    observed=float(units.mean())
    if len(units)<5:return np.nan,len(units),observed
    rng=np.random.default_rng(20260910)
    null=[]
    for _ in range(20):
        null.extend((rng.choice([-1.,1.],size=(500,len(units)))*units).mean(axis=1))
    p=(1+np.count_nonzero(np.asarray(null)>=observed))/(len(null)+1)
    return float(p),len(units),observed


def holm(values):
    out=np.full(len(values),np.nan)
    v=np.asarray(values,float);indices=np.flatnonzero(np.isfinite(v))
    order=indices[np.argsort(v[indices])]
    running=0.
    for rank,i in enumerate(order):
        running=max(running,min(1.,float(v[i])*(len(order)-rank)))
        out[i]=running
    return out


def rank_auc(score,label):
    score=pd.Series(np.asarray(score,float));label=np.asarray(label,bool)
    valid=np.isfinite(score);score=score[valid];label=label[valid]
    n1=int(label.sum());n0=len(label)-n1
    if not n1 or not n0:return np.nan
    return float((score.rank(method='average').to_numpy()[label].sum()-n1*(n1+1)/2)/(n1*n0))


def make_regime(daily):
    if daily.empty:return pd.DataFrame(columns=['time','breadth','md_positive','n_assets'])
    daily['time']=pd.to_datetime(daily.time,utc=True)
    d=scope_rows(daily,'combined').sort_values(['time','asset','quote_volume_24h','venue'],ascending=[True,True,False,True])
    d=d.drop_duplicates(['time','asset'])
    return d.groupby('time',as_index=False).agg(breadth=('above_sma60','mean'),md_positive=('md_positive','mean'),n_assets=('asset','nunique'))


def add_regime(events,regime):
    if events.empty:return events
    g=events.sort_values('decision_time').copy()
    if regime.empty:g['regime_breadth']=np.nan;return g
    r=regime.rename(columns={'time':'regime_asof','breadth':'regime_breadth'})
    return pd.merge_asof(g,r.sort_values('regime_asof'),left_on='decision_time',right_on='regime_asof',direction='backward')


def summarize_events(events):
    rows=[];scores=[];diagnostics=[]
    for scope in SCOPES:
        pool=scope_rows(events,scope)
        for minutes in (60,240):
            for arm in ARMS:
                for period,(start,end) in PERIODS.items():
                    g=pool.loc[pool.minutes.eq(minutes)&pool.arm.eq(arm)&pool.decision_time.ge(start)&pool.decision_time.lt(end)]
                    valid=g.loc[g.valid].copy()
                    p,nunits,paired=paired_test(valid)
                    wins=valid.net_bp>0
                    rows.append(dict(scope=scope,minutes=minutes,arm=arm,period=period,events=len(g),valid_events=len(valid),
                        mean_net_bp=valid.net_bp.mean(),median_net_bp=valid.net_bp.median(),mean_net_r=valid.net_r.mean(),
                        win_rate=wins.mean(),mean_control_bp=valid.control_mean_net_bp.mean(),mean_excess_bp=valid.excess_bp.mean(),
                        permutation_p=p,permutation_assets=nunits,asset_balanced_excess_bp=paired,
                        matched_fraction=valid.control_count.gt(0).mean(),mean_mfe_pct=valid.mfe_return.mean()*100,
                        realized_20pct_rate=valid.net_return.ge(.2).mean(),realized_50pct_rate=valid.net_return.ge(.5).mean(),
                        initial_risk_pct_median=valid.initial_risk_frac.median()*100,censored=int(valid.censored.sum())))
                    if period=='full':
                        for feature in (*SCORES,'higher_permission','regime_breadth'):
                            if feature not in valid:continue
                            x=pd.to_numeric(valid[feature],errors='coerce')
                            if feature=='relative_volume':bins=[-np.inf,1,2,4,np.inf]
                            elif feature=='tr_expansion':bins=[-np.inf,1,2,3,np.inf]
                            elif feature=='momentum10':bins=[-np.inf,0,60,90,np.inf]
                            elif feature=='near_zero_bars':bins=[-np.inf,12,24,48,np.inf]
                            elif feature=='prior24h_return':bins=[-np.inf,0,.1,.3,np.inf]
                            elif feature=='six_ma_width_atr':bins=[-np.inf,1,2,3,np.inf]
                            elif feature=='regime_breadth':bins=[-np.inf,.3,.6,np.inf]
                            else:bins=[-np.inf,.5,np.inf]
                            buckets=pd.cut(x,bins,right=False).astype(str).where(x.notna(),'unknown')
                            for bucket,h in valid.groupby(buckets,observed=True):
                                diagnostics.append(dict(scope=scope,minutes=minutes,arm=arm,feature=feature,bucket=bucket,n=len(h),
                                    win_rate=h.net_bp.gt(0).mean(),mean_net_bp=h.net_bp.mean(),mean_mfe_pct=h.mfe_return.mean()*100,
                                    mean_excess_bp=h.excess_bp.mean(),realized_20pct_rate=h.net_return.ge(.2).mean()))
                    if len(valid)>=20:
                        for feature in SCORES:
                            x=pd.to_numeric(valid[feature],errors='coerce')
                            ranked=valid.loc[x.notna()].copy()
                            if len(ranked)<20:continue
                            ranked['score']=x.loc[ranked.index]*(-1 if feature=='six_ma_width_atr' else 1)
                            # Stable tied scores cannot be rearranged by outcomes.
                            ranked=ranked.sort_values(['score','event_id'],ascending=[False,True])
                            top=ranked.iloc[:max(1,int(np.ceil(len(ranked)*.1)))]
                            scores.append(dict(scope=scope,minutes=minutes,arm=arm,period=period,feature=feature,n=len(ranked),top_n=len(top),
                                descriptive_auc=rank_auc(ranked.score,ranked.net_bp.gt(0)),top_decile_gross_bp=top.gross_bp.mean(),
                                top_decile_net_bp=top.net_bp.mean(),top_decile_win_rate=top.net_bp.gt(0).mean(),
                                top_decile_excess_bp=top.excess_bp.mean()))
    summary=pd.DataFrame(rows);summary['holm_p']=holm(summary.permutation_p)
    return summary,pd.DataFrame(diagnostics),pd.DataFrame(scores)


def portfolio_periods(path,selected,scope,minutes,arm):
    rows=[]
    for period,(start,end) in PERIODS.items():
        p=path.loc[path.time.gt(start)&path.time.le(end)].copy()
        before=path.loc[path.time.le(start)]
        opening=float(before.equity.iloc[-1]) if len(before) else 100000.
        p['drawdown']=p.equity/np.maximum.accumulate(np.r_[opening,p.equity.to_numpy()])[1:]-1
        s=selected.loc[selected.entry_time.ge(start)&selected.entry_time.lt(end)].copy() if not selected.empty else selected
        row=summarize_portfolio(p,s,opening)
        if period!='full':
            for name in ('top1_positive_profit_share','top5_positive_profit_share','return_minus_top1_contribution_pct','return_minus_top5_contribution_pct'):
                row[name]=np.nan
        row.update(scope=scope,minutes=minutes,arm=arm,period=period,opening_equity=opening,
            closing_equity=float(p.equity.iloc[-1]),mdd_definition='close_equity',cohort_stats='entry cohort, possibly exits after subperiod')
        rows.append(row)
    return rows


def make_portfolios(events,controls,out,price_map):
    rows=[];actual_selections=[]
    for scope in SCOPES:
        pool=scope_rows(events,scope);ctl=scope_rows(controls,scope)
        for minutes in (60,240):
            prices=price_map[minutes]
            for arm in ARMS:
                g=pool.loc[pool.minutes.eq(minutes)&pool.arm.eq(arm)].copy()
                path,selected=run_portfolio(g,prices,START,END,minutes)
                destination=out/'portfolios';destination.mkdir(exist_ok=True)
                path.to_csv(destination/f'{scope}_{minutes}_{arm}.csv.gz',index=False)
                selected.to_csv(destination/f'{scope}_{minutes}_{arm}_selected.csv.gz',index=False)
                summaries=portfolio_periods(path,selected,scope,minutes,arm)
                random=[]
                for number in (0,1,2):
                    c=ctl.loc[ctl.minutes.eq(minutes)&ctl.arm.eq(arm)&ctl.control_number.eq(number)].copy()
                    if c.empty:continue
                    cp,cs=run_portfolio(c,prices,START,END,minutes)
                    cp.to_csv(destination/f'{scope}_{minutes}_{arm}_random{number}.csv.gz',index=False)
                    random.extend(portfolio_periods(cp,cs,scope,minutes,arm))
                rr=pd.DataFrame(random)
                for s in summaries:
                    comparable=rr.loc[rr.period.eq(s['period'])] if not rr.empty else rr
                    s['random_schedules']=len(comparable)
                    s['random_return_pct_mean']=comparable.return_pct.mean() if len(comparable) else np.nan
                    s['random_return_pct_min']=comparable.return_pct.min() if len(comparable) else np.nan
                    s['random_return_pct_max']=comparable.return_pct.max() if len(comparable) else np.nan
                    s['actual_matching_fraction']=g.control_count.gt(0).mean() if len(g) else np.nan
                rows.extend(summaries)
                selected['scope']=scope
                actual_selections.append(selected)
        print(json.dumps({'portfolio_scope_complete':scope}),flush=True)
    return pd.DataFrame(rows),pd.concat(actual_selections,ignore_index=True)


def opportunity_audit(calendar,events):
    """Only labels use future high prices; signal/cash selection is unchanged."""
    rows=[]
    if calendar.empty:return pd.DataFrame(columns=['venue','symbol','asset','kind','window_start','window_end','peak_time','peak_return','close_return','arm','result','overlapping_events','examples','note'])
    # >=50% fixed-calendar excursions, including the separately flagged 100%.
    big=calendar.loc[calendar.peak_return.ge(.5)].copy()
    for _,event in big.iterrows():
        same=events.loc[events.instrument.eq(event.instrument)&events.valid&events.minutes.eq(60)]
        for arm in ('focus_sma60','dense_sma60','pullback_sma60','young_breakout_sma20'):
            g=same.loc[same.arm.eq(arm)]
            active=g.loc[g.entry_time.le(event.peak_time)&g.exit_time_upper.gt(event.window_start)]
            # A trade entered before the calendar week can capture the rally;
            # a new signal after the peak cannot be retroactively counted.
            held_peak=active.loc[active.exit_time_lower.ge(event.peak_time+pd.Timedelta(hours=1))]
            uncertain=active.loc[active.exit_time_upper.gt(event.peak_time)&active.exit_time_lower.lt(event.peak_time+pd.Timedelta(hours=1))]
            outcome=('no_prior_entry' if active.empty else 'held_full_peak_bar' if not held_peak.empty
                else 'peak_bar_order_unknown' if not uncertain.empty else 'exited_before_peak')
            rows.append(dict(venue=event.venue,symbol=event.symbol,asset=event.asset,kind=event.kind,
                window_start=event.window_start,window_end=event.window_end,peak_time=event.peak_time,peak_return=event.peak_return,
                close_return=event.close_return,arm=arm,result=outcome,overlapping_events=len(active),
                examples='|'.join(active.event_id.head(4)),note='event-path opportunity, not cash-constrained fill'))
    return pd.DataFrame(rows)


def load_products(results):
    coverage=[];batches={k:[] for k in ('events','controls','calendar_events','daily_context')}
    schemas={}
    filenames={'events':'events.csv.gz','controls':'controls.csv.gz','calendar_events':'calendar.csv.gz','daily_context':'daily_context.csv'}
    for meta in sorted((results/'markets').glob('*/*/coverage.json')):
        item=json.loads(meta.read_text());coverage.append(item)
        if item.get('errors'):raise ValueError('Dataset error must be resolved or explicitly excluded: '+str(meta))
        for artifact in item['artifacts']:
            p=Path(artifact['path'])
            if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=artifact['sha256']:
                raise ValueError('Dataset artifact no longer matches frozen SHA: '+str(p))
        for kind in batches:
            path=meta.parent/filenames[kind]
            if path.exists() and path.stat().st_size:
                try:f=read_events(path)
                except pd.errors.EmptyDataError:continue
                schemas[kind]=f.iloc[:0]
                if not f.empty:batches[kind].append(f)
    combined={kind:pd.concat(frames,ignore_index=True) if frames else schemas.get(kind,pd.DataFrame()) for kind,frames in batches.items()}
    return pd.DataFrame(coverage),combined


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=EXPERIMENT/'results')
    parser.add_argument('--data',type=Path,default=EXPERIMENT/'data')
    args=parser.parse_args()
    for p in (Path(__file__),ROOT/'yoyo/evaluation/altseason_portfolio.py'):
        if subprocess.check_output(['git','show','HEAD:'+str(p.relative_to(ROOT))],cwd=ROOT)!=p.read_bytes():raise ValueError('Commit builder before scoring')
    collections={}
    for venue in SCOPES[:-1]:
        path=args.data/f'{venue}_collection_manifest.json'
        if not path.exists():raise ValueError('Collection incomplete: '+venue)
        collections[venue]=json.loads(path.read_text())
    coverage,products=load_products(args.results)
    if coverage.empty:raise ValueError('No evaluated market coverage')
    for venue,collection in collections.items():
        acquired={x['request_spec']['symbol'] for x in collection['completed']}
        evaluated=set(coverage.loc[coverage.venue.eq(venue),'symbol'])
        if acquired-evaluated:raise ValueError('Unprocessed acquired markets: '+str(sorted(acquired-evaluated)))
        dataset_manifest=json.loads((args.results/(venue+'_dataset_manifest.json')).read_text())
        if dataset_manifest['skipped'] or any(x['errors'] for x in dataset_manifest['completed']):
            raise ValueError('Dataset exclusions/errors require explicit review: '+venue)
    events,controls=products['events'],products['controls']
    if events.empty:raise ValueError('No events; inspect coverage, not silently zero-profit')
    regime=make_regime(products['daily_context']);regime.to_csv(args.results/'regime.csv',index=False)
    events=add_regime(events,regime)
    for name,f in [('events',events),('controls',controls),('calendar_events',products['calendar_events'])]:f.to_csv(args.results/(name+'.csv.gz'),index=False)
    coverage.to_csv(args.results/'coverage.csv',index=False)
    summary,diagnostics,scores=summarize_events(events)
    summary.to_csv(args.results/'summary.csv',index=False)
    diagnostics.to_csv(args.results/'feature_diagnostics.csv',index=False)
    scores.to_csv(args.results/'score_diagnostics.csv',index=False)
    price_map={60:{},240:{}}
    references=pd.concat([events[['instrument','minutes','features_path']],controls[['instrument','minutes','features_path']]]).drop_duplicates()
    for row in references.itertuples():
        f=pd.read_pickle(row.features_path)
        price_map[int(row.minutes)][row.instrument]=f[['open','close']]
    ps,selected=make_portfolios(events,controls,args.results,price_map)
    ps.to_csv(args.results/'portfolio_summary.csv',index=False)
    selected.to_csv(args.results/'portfolio_selections.csv.gz',index=False)
    opportunity_audit(products['calendar_events'],events).to_csv(args.results/'opportunity_audit.csv.gz',index=False)
    manifest=dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),start=str(START),split=str(SPLIT),end=str(END),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),holdout_consumption=1,
        historical_exploration=True,optimization=False,live_changes=False,events=len(events),controls=len(controls),markets=len(coverage),
        venues={k:dict(eligible=v['eligible'],completed=len(v['completed']),errors=len(v['errors'])) for k,v in collections.items()},
        fees='20bp static entry notional; full funding/impact excluded from base account',
        control_protocol='three matched schedules, variable availability; asset clustered paired test; no same-asset price paths treated as independent',
        identity='Exact base names only; unverified numeric multiplier aliases excluded from combined account',
        source_survivorship='Current catalogs plus available nontrading Binance history; not complete historical listings')
    (args.results/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest),flush=True)


if __name__=='__main__':main()
