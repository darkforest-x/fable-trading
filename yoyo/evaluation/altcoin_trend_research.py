"""Preregistered, offline altcoin trend experiments; no execution or live IO.

See exp-imacd-altcoin-trends-20260909-v1/PROJECT_PLAN.md. Features at a
close use no later bar; Monday membership uses the prior-seven-day feature
at that Monday's OPEN and remains fixed all week. Return labels alone use
future bars. The main 54-symbol convenience pool is frozen; Owner-selected
SOPH/USELESS examples never affect ranks or candidate selection. Development
and audit runs use separate directories/cutoffs. Costs are the unchanged
20bp convention; derivative features are conservative historical diagnostics,
not proof of actual historical first-seen times or executable net returns.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.altcoin_features import IMACDParams, build_altcoin_features
from yoyo.data.altcoin_derivatives import build_derivative_features
from yoyo.evaluation.imacd_formation_research import aggregate
from yoyo.evaluation.imacd_startup_research import inference, ranking
from yoyo.evaluation.altcoin_accounting import evaluate_events, compound_portfolio

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-imacd-altcoin-trends-20260909-v1'
SEED = 20260909
COMMON_WARMUP = 550
END = '2026-09-08T16:00:00Z'
FOLDS = {
    'development': [('development', '2023-01-01', '2025-01-01'),
                    ('validation', '2025-01-01', '2026-01-01')],
    'audit': [('audit_pre', '2026-01-01', '2026-05-04'),
              ('audit_seen', '2026-05-04', '2026-07-12'),
              ('recent_test', '2026-07-12', END)],
}
PARAMS = {'base': IMACDParams()}
for _name, _field, _value in [
    ('ma21', 'length_ma', 21), ('ma55', 'length_ma', 55),
    ('signal5', 'length_signal', 5), ('signal13', 'length_signal', 13),
    ('focus6', 'focus_min_bars', 6), ('focus24', 'focus_min_bars', 24),
    ('focus36', 'focus_min_bars', 36), ('band005', 'focus_atr_band', .05),
    ('band020', 'focus_atr_band', .20), ('band030', 'focus_atr_band', .30),
]:
    PARAMS[_name] = IMACDParams(**{**asdict(IMACDParams()), _field: _value})
ARMS = [{'arm': 'base', 'parameter': 'base', 'exit': 'md', 'gate': 'all', 'family': 'baseline'}]
ARMS += [dict(arm=p, parameter=p, exit='md', gate='all', family='parameter') for p in PARAMS if p != 'base']
ARMS += [dict(arm='gate_'+g, parameter='base', exit='md', gate=g, family='context')
         for g in ['rvol2', 'tr15', 'close70', 'box_break', 'bb20']]
ARMS += [dict(arm='exit_'+e, parameter='base', exit=e, gate='all', family='exit')
         for e in ['fixed3r', 'chandelier', 'sma60']]
DERIVATIVE_ARMS = [dict(arm='deriv_'+g, parameter='base', exit='md', gate=g, family='derivative')
                   for g in ['oi24_pos', 'oi4_pos', 'taker55', 'oi24_down']]
for _arm in ARMS: _arm['domain']='all'
for _arm in DERIVATIVE_ARMS:
    _arm['domain']='oi24' if 'oi24' in _arm['gate'] else 'oi4' if 'oi4' in _arm['gate'] else 'taker'
DERIVATIVE_ARMS += [dict(arm='deriv_'+d+'_available',parameter='base',exit='md',gate='all',family='derivative_baseline',domain=d)
                    for d in ['oi24','oi4','taker']]
CONTEXT_COLUMNS = ['near_zero_bars', 'relative_volume', 'tr_expansion', 'close_location',
                   'body_fraction', 'momentum3_atr', 'bb_width_rank_prior240',
                   'release_zone_high', 'release_zone_low', 'atr_pct']
DERIVATIVE_COLUMNS = ['oi_base_log_change_4h', 'oi_base_log_change_24h',
                      'oi_usd_log_change_24h', 'taker_buy_share', 'funding_last_rate']


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_seed(*parts) -> int:
    return (SEED + int(hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:8], 16)) % 2**32


def dump_json(path: Path, value) -> None:
    def default(x):
        if isinstance(x, (np.integer, np.floating, np.bool_)):
            return x.item()
        if isinstance(x, (pd.Timestamp, Path)):
            return str(x)
        raise TypeError(type(x).__name__)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=default, allow_nan=False)+'\n')


def committed_sources() -> dict:
    """Reject building with source that is absent from, or differs from HEAD."""
    names = [Path(__file__), ROOT/'yoyo/data/altcoin_features.py',
             ROOT/'yoyo/data/altcoin_derivatives.py', ROOT/'yoyo/evaluation/altcoin_accounting.py',
             ROOT/'yoyo/evaluation/imacd_formation_research.py', ROOT/'yoyo/evaluation/imacd_startup_research.py',
             EXPERIMENT/'PROJECT_PLAN.md']
    hashes = {}
    for p in names:
        rel = str(p.resolve().relative_to(ROOT))
        saved = subprocess.check_output(['git', 'show', 'HEAD:'+rel], cwd=ROOT)
        if saved != p.read_bytes():
            raise ValueError('Commit builder before evaluation: '+rel)
        hashes[rel] = sha(p)
    return dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(), hashes=hashes)


def read_history(record, end) -> pd.DataFrame:
    p = Path(record['output_path'])
    if not p.is_absolute():
        p = ROOT/p
    if sha(p) != record['output_sha256']:
        raise ValueError('History identity changed: '+str(p))
    # ts-first prefix prevents parsing later prices in a development run.
    from yoyo.evaluation.imacd_formation_research import read_prefix
    return read_prefix(p, end=end)


def week_start(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.normalize() - pd.to_timedelta(index.dayofweek, unit='D')


def weekly_membership(feature_by_symbol: dict[str, pd.DataFrame], *, fraction=.25, min_symbols=10) -> pd.DataFrame:
    """Rank fully warmed symbols before Monday's first candle; freeze week.

    Inputs need only prior7d_atr_pct_mean and ready. No current-week candle,
    outcome, current gainers list or dollar-volume proxy is consulted.
    """
    rows = []
    for symbol, f in sorted(feature_by_symbol.items()):
        if symbol in ('BTC', 'ETH', 'SOPH', 'USELESS'):
            continue
        monday = (f.index.dayofweek == 0) & (f.index.hour == 0) & (f.index.minute == 0)
        valid = monday & (np.arange(len(f)) >= COMMON_WARMUP) & f.ready.to_numpy(bool)
        for i in np.flatnonzero(valid):
            value = float(f.prior7d_atr_pct_mean.iloc[i])
            if np.isfinite(value) and value > 0:
                rows.append(dict(week=f.index[i], symbol=symbol, prior_vol=value))
    output = []
    if not rows:
        return pd.DataFrame(columns=['week','symbol','prior_vol','rank','eligible_count','high_vol'])
    for week, g in pd.DataFrame(rows).groupby('week', sort=True):
        g = g.sort_values(['prior_vol','symbol'], ascending=[False,True]).copy()
        g['rank'] = np.arange(1,len(g)+1)
        g['eligible_count'] = len(g)
        g['high_vol'] = (g['rank'] <= math.ceil(len(g)*fraction)) & (len(g) >= min_symbols)
        output.append(g)
    return pd.concat(output, ignore_index=True)


def membership_mask(index, symbol, members, *, minutes):
    """Use the week of the close-confirmed decision / next-open entry.

    The Sunday final candle closes at Monday 00:00. Its completed data is
    already included in Monday's prior-week ranking, so that new membership
    governs this decision, not the week of the signal candle's open.
    """
    decision_closes = index + pd.Timedelta(minutes=minutes)
    selected = set(pd.to_datetime(members.loc[(members.symbol == symbol) & members.high_vol, 'week'], utc=True))
    return np.asarray([x in selected for x in week_start(decision_closes)], dtype=bool)


def gate_mask(f: pd.DataFrame, gate: str) -> np.ndarray:
    side = f.release_side.to_numpy(int)
    if gate == 'all':
        return np.ones(len(f), dtype=bool)
    if gate == 'rvol2': return f.relative_volume.ge(2).to_numpy()
    if gate == 'tr15': return f.tr_expansion.ge(1.5).to_numpy()
    if gate == 'close70': return np.where(side > 0, f.close_location >= .7, f.close_location <= .3)
    if gate == 'box_break': return np.where(side > 0, f.close > f.release_zone_high, f.close < f.release_zone_low)
    if gate == 'bb20': return f.bb_width_rank_prior240.le(.2).to_numpy()
    if gate == 'oi24_pos': return f.oi_base_log_change_24h.gt(0).to_numpy()
    if gate == 'oi4_pos': return f.oi_base_log_change_4h.gt(0).to_numpy()
    if gate == 'oi24_down': return f.oi_base_log_change_24h.lt(0).to_numpy()
    if gate == 'taker55': return np.where(side > 0, f.taker_buy_share >= .55, f.taker_buy_share <= .45)
    raise ValueError('Unknown gate '+gate)


def matched_controls(f, eligible, *, seed):
    """Outcome-blind same-month/volatility-quintile/direction controls.

    One source symbol/TF/cohort per call. Controls exclude any release of the
    current parameterization and are sampled without reuse in this mapping.
    Selecting a gated arm uses this same mapping, not a newly favorable pool.
    """
    month = f.index.strftime('%Y-%m').to_numpy()
    volbin = (f.atr_pct.rolling(240, min_periods=240).rank(pct=True)*5).apply(np.ceil).clip(1,5).fillna(-1).to_numpy(int)
    direction = np.sign(f.md.fillna(0).to_numpy()).astype(int)
    release = f.release_side.to_numpy(int)
    pools = {}
    for i in np.flatnonzero(eligible & (release == 0) & (direction != 0)):
        if volbin[i] < 0: continue
        pools.setdefault((month[i],volbin[i],direction[i]),[]).append(int(i))
    rng = np.random.default_rng(seed)
    for v in pools.values(): rng.shuffle(v)
    result = {}
    for i in np.flatnonzero(eligible & (release != 0)):
        p = pools.get((month[i],volbin[i],release[i]),[])
        result[int(i)] = [p.pop() for _ in range(min(3,len(p)))]
    return result


def add_derivatives(f, symbol, minutes, folder):
    sources = {}
    for kind in ('oi','taker','funding'):
        path = folder/'normalized'/f'{symbol}-USDT-SWAP_4H_{kind}.csv'
        if path.exists():
            value = pd.read_csv(path)
            for column in ('event_time','available_at_nominal','funding_time'):
                if column in value: value[column] = pd.to_datetime(value[column],unit='ms',utc=True)
            sources[kind] = value
    d = build_derivative_features(f.index+pd.Timedelta(minutes=minutes), inst_id=symbol+'-USDT-SWAP', **sources)
    d.index = f.index
    return pd.concat([f,d],axis=1)


def _cohorts(symbol, record, highvol):
    if record['cohort'] == 'owner_illustration':
        return {'owner_illustration':np.ones(len(highvol),bool)}
    result = {'all_core':np.ones(len(highvol),bool)}
    if symbol in ('BTC','ETH'): result['majors'] = np.ones(len(highvol),bool)
    else: result['high_vol'] = highvol
    return result


def evaluate_symbol(symbol, record, minutes, feature_paths, members, folds, outdir, derivative_dir=None, allowed_arms=None):
    """Evaluate fixed arms; outputs preserve every case and control outcome."""
    output_cases, output_controls, curves, diagnostics = [], [], {}, []
    derivative_dir = derivative_dir if minutes in (60,240) else None
    arms = [a for a in ARMS if allowed_arms is None or a['arm'] in allowed_arms]
    arms += DERIVATIVE_ARMS if derivative_dir is not None else []
    for (parameter,domain), grouped in pd.DataFrame(arms).groupby(['parameter','domain'],sort=False):
        if parameter in feature_paths:
            f = pd.read_pickle(feature_paths[parameter], compression='gzip')
        else:
            base_frame = pd.read_pickle(feature_paths['base'], compression='gzip')
            raw = base_frame[['open','high','low','close','volume']]
            f = build_altcoin_features(raw,PARAMS[parameter])
            for col in raw: f[col]=raw[col]
        b = f[['open','high','low','close','volume']]
        b.attrs['period_seconds'] = minutes * 60
        if parameter == 'base' and derivative_dir is not None:
            f = add_derivatives(f,symbol,minutes,derivative_dir)
        highvol = membership_mask(f.index,symbol,members,minutes=minutes)
        for fold,start,end in folds:
            start,end = pd.Timestamp(start,tz='UTC'), pd.Timestamp(end) if str(end).endswith('Z') else pd.Timestamp(end,tz='UTC')
            locs = np.flatnonzero((b.index >= start) & (b.index+pd.Timedelta(minutes=minutes) <= end))
            if len(locs)<2: continue
            first,last = int(locs[0]),int(locs[-1])
            valid = (np.arange(len(f)) >= max(first,COMMON_WARMUP)) & (np.arange(len(f)) < last) & f.ready.to_numpy(bool) & f.atr.gt(0).to_numpy()
            if domain!='all':
                column={'oi24':'oi_base_log_change_24h','oi4':'oi_base_log_change_4h','taker':'taker_buy_share'}[domain]
                valid &= f[column].notna().to_numpy()
            for cohort, cmask in _cohorts(symbol,record,highvol).items():
                eligible = valid & cmask
                mapping = matched_controls(f,eligible,seed=stable_seed(symbol,minutes,fold,cohort,parameter,domain))
                release = np.flatnonzero(eligible & f.release_side.ne(0).to_numpy())
                controls = np.asarray(sorted({i for v in mapping.values() for i in v}),int)
                for exit_rule, subarms in grouped.groupby('exit',sort=False):
                    cases = evaluate_events(b,f,release,f.release_side.iloc[release].to_numpy(int),first_i=first,last_i=last,exit_rule=exit_rule)
                    control = evaluate_events(b,f,controls,np.sign(f.md.iloc[controls]).to_numpy(int),first_i=first,last_i=last,exit_rule=exit_rule)
                    invalid_cases = int((~cases.valid).sum()) if len(cases) else 0
                    invalid_controls = int((~control.valid).sum()) if len(control) else 0
                    cases = cases.loc[cases.valid].copy()
                    control = control.loc[control.valid].copy()
                    if len(control):
                        control = control.set_index('signal_i',drop=False)
                        control['parameter'],control['exit_rule'] = parameter,exit_rule
                        control['domain'] = domain
                        control['symbol'],control['minutes'],control['fold'],control['cohort'] = symbol,minutes,fold,cohort
                        output_controls.append(control.reset_index(drop=True))
                    for arm in subarms.to_dict('records'):
                        if len(cases):
                            q = cases.loc[gate_mask(f,arm['gate'])[cases.signal_i.to_numpy(int)]].copy()
                        else: q = cases.copy()
                        if len(q):
                            selected_i = q.signal_i.to_numpy(int)
                            for c in CONTEXT_COLUMNS + (DERIVATIVE_COLUMNS if parameter == 'base' and derivative_dir else []):
                                q[c] = f[c].iloc[selected_i].to_numpy()
                            q['control_indexes'] = [' '.join(map(str,mapping.get(int(i),[]))) for i in selected_i]
                            q['control_count'] = [sum(j in control.index for j in mapping.get(int(i),[])) if len(control) else 0 for i in selected_i]
                            q['control_mean_net_bp'] = [control.loc[[j for j in mapping.get(int(i),[]) if j in control.index],'net_bp'].mean() if len(control) else np.nan for i in selected_i]
                            q['excess_bp'] = q.net_bp-q.control_mean_net_bp
                            q['symbol'],q['minutes'],q['fold'],q['cohort'],q['arm'] = symbol,minutes,fold,cohort,arm['arm']
                            q['month'] = b.index[selected_i].strftime('%Y-%m')
                            q['event_id'] = [f'{symbol}_{minutes}_{parameter}_{i}' for i in selected_i]
                            q['decision_close_time'] = b.index[selected_i]+pd.Timedelta(minutes=minutes)
                        equity,selected,diag = compound_portfolio(b,q,first_i=first,last_i=last)
                        adverse = diag.pop('adverse_equity', None)
                        key = '|'.join([fold,cohort,arm['arm']])
                        curves[key] = equity
                        if adverse is not None:
                            curves[key+'|adverse'] = adverse
                        diagnostics.append(dict(symbol=symbol,minutes=minutes,fold=fold,cohort=cohort,arm=arm['arm'],invalid_cases_before_gate=invalid_cases,invalid_controls=invalid_controls,**diag))
                        if len(q):
                            q['portfolio_selected'] = q.signal_i.isin(selected.signal_i if len(selected) else [])
                            output_cases.append(q)
    target = outdir/f'{symbol}_{minutes}'
    target.mkdir(parents=True,exist_ok=False)
    pd.concat(output_cases,ignore_index=True).to_csv(target/'events.csv.gz',index=False) if output_cases else pd.DataFrame().to_csv(target/'events.csv.gz',index=False)
    pd.concat(output_controls,ignore_index=True).to_csv(target/'controls.csv.gz',index=False) if output_controls else pd.DataFrame().to_csv(target/'controls.csv.gz',index=False)
    pd.DataFrame(curves).to_pickle(target/'curves.pkl.gz',compression='gzip')
    dump_json(target/'diagnostics.json',diagnostics)
    return dict(symbol=symbol,minutes=minutes,event_file=str(target/'events.csv.gz'),curve_file=str(target/'curves.pkl.gz'),case_rows=sum(len(x) for x in output_cases))


def holm(values, family_size=None):
    a=np.asarray(values,float); out=np.full(len(a),np.nan); finite=np.flatnonzero(np.isfinite(a))
    size=len(a) if family_size is None else family_size
    if size<len(a): raise ValueError('family_size cannot shrink the predeclared family')
    order=finite[np.argsort(a[finite])]; last=0.
    for rank,i in enumerate(order):
        last=max(last,min(1.,a[i]*(size-rank)));out[i]=last
    return out


def summarize(outdir: Path, records, periods, folds):
    event_frames=[]; curves={}; empty=[]
    for record in records:
        symbol=record['symbol']
        for minutes in periods:
            p=outdir/f'{symbol}_{minutes}'
            if not (p/'diagnostics.json').exists():
                continue
            try: e=pd.read_csv(p/'events.csv.gz')
            except pd.errors.EmptyDataError: e=pd.DataFrame()
            if len(e): event_frames.append(e)
            frame=pd.read_pickle(p/'curves.pkl.gz',compression='gzip')
            for key in frame:
                if key.endswith('|adverse'):
                    continue
                fold,cohort,arm=key.split('|')
                curves.setdefault((fold,minutes,cohort,arm),{})[symbol]=frame[key].dropna()
    events=pd.concat(event_frames,ignore_index=True) if event_frames else pd.DataFrame()
    events.to_csv(outdir/'events_all.csv.gz',index=False)
    summary=[]; ranks=[]; portfolio_lines={}
    for key, per_symbol in sorted(curves.items()):
        fold,minutes,cohort,arm=key
        times=next((s,e) for name,s,e in folds if name==fold)
        start=pd.Timestamp(times[0],tz='UTC');end=pd.Timestamp(times[1]) if str(times[1]).endswith('Z') else pd.Timestamp(times[1],tz='UTC')
        calendar=pd.date_range(start+pd.Timedelta(minutes=minutes),end,freq=f'{minutes}min')
        denom=2 if cohort in ('majors','owner_illustration') else 54
        sums=pd.Series(0.,index=calendar)
        for s in per_symbol.values():
            # Initial missing history is idle cash; later endpoint remains the last equity.
            curve=s.reindex(calendar).ffill().fillna(1.)
            sums+=curve
        equity=(sums+denom-len(per_symbol))/denom
        portfolio_lines['|'.join(map(str,key))]=equity
        g=events.loc[(events.fold==fold)&(events.minutes==minutes)&(events.cohort==cohort)&(events.arm==arm)] if len(events) else pd.DataFrame()
        selected=g.loc[g.portfolio_selected] if len(g) else g
        net=g.net_bp if len(g) else pd.Series(dtype=float)
        matched=g.loc[g.excess_bp.notna()] if len(g) else g
        top=g.sort_values(['net_bp','event_id'],ascending=[False,True]).head(3) if len(g) else g
        profit=net.clip(lower=0).sum();loss=-net.clip(upper=0).sum()
        row=dict(fold=fold,minutes=minutes,cohort=cohort,arm=arm,n=len(g),symbols=g.symbol.nunique() if len(g) else 0,
                 selected_n=len(selected),matched_n=len(matched),matched_symbols=matched.symbol.nunique() if len(matched) else 0,cost_bp=20,funding_included=False,
                 mean_gross_bp=g.gross_bp.mean() if len(g) else np.nan,mean_net_bp=net.mean(),median_net_bp=net.median(),
                 win_pct=net.gt(0).mean()*100 if len(net) else np.nan,profit_factor=profit/loss if loss else np.nan,
                 censored_n=int(g.censored.sum()) if len(g) else 0,
                 control_net_bp=matched.control_mean_net_bp.mean() if len(matched) else np.nan,
                 mean_net_ex_top3_bp=g.loc[~g.index.isin(top.index)].net_bp.mean() if len(g)>3 else np.nan,
                 top3_positive_profit_pct=top.net_bp.clip(lower=0).sum()/profit*100 if profit else np.nan,
                 median_mfe_r=g.mfe_r.median() if len(g) else np.nan,median_net_r=g.net_r.median() if len(g) else np.nan,
                 portfolio_net_pct=(equity.iloc[-1]-1)*100,mdd_pct=(equity/equity.cummax().clip(lower=1)-1).min()*100,
                 capital_sleeves=denom,**inference(matched.excess_bp,matched.month) if len(matched) else dict(excess_bp=np.nan,p=np.nan,ci_low=np.nan,ci_high=np.nan,months=0))
        summary.append(row)
        if len(g):
            for score in ['relative_volume','tr_expansion','near_zero_bars']:
                r=ranking(g,score);r.update(fold=fold,minutes=minutes,cohort=cohort,arm=arm,score=score);ranks.append(r)
    summary=pd.DataFrame(summary)
    family={a['arm']:a['family'] for a in ARMS+DERIVATIVE_ARMS}
    summary['family']=summary.arm.map(family)
    summary['p_holm']=np.nan
    if 'p' in summary:
        for _,g in summary.groupby(['fold','minutes','cohort','family']): summary.loc[g.index,'p_holm']=holm(g.p)
        for _,g in summary.loc[(summary.cohort=='high_vol') & summary.minutes.isin([60,240]) & summary.family.isin(['parameter','context','exit'])].groupby('fold'):
            summary.loc[g.index,'p_holm_primary']=holm(g.p,family_size=max(36,len(g)))
    summary.to_csv(outdir/'summary.csv',index=False)
    pd.DataFrame(ranks).to_csv(outdir/'rank_diagnostics.csv',index=False)
    pd.DataFrame(portfolio_lines).to_pickle(outdir/'portfolio_curves.pkl.gz',compression='gzip')
    return summary


def lock_selection(summary, outdir):
    """Validation ranking only after fixed development/coverage gates."""
    selections=[]
    families={a['arm']:a['family'] for a in ARMS}
    for minutes in sorted(summary.minutes.unique()):
        q=summary.loc[(summary.minutes==minutes)&(summary.cohort=='high_vol')]
        good=[]
        for arm,g in q.groupby('arm'):
            if set(g.fold)!= {'development','validation'}: continue
            if len(g)!=2: raise ValueError('selection requires exactly two development folds')
            if ((g.matched_n>=50)&(g.matched_symbols>=10)&(g.mean_net_bp>=0)&(g.excess_bp>=0)).all():
                v=g.loc[g.fold=='validation'].iloc[0];good.append((arm,float(v.portfolio_net_pct),float(v.mdd_pct)))
        good.sort(key=lambda x:(-x[1],-x[2],x[0]))
        family_choices={family:next((a for a,_,_ in good if families[a]==family),None) for family in ['parameter','context','exit']}
        allowed=sorted({'base','exit_fixed3r','exit_chandelier','exit_sma60'} | {a for a in family_choices.values() if a} | ({good[0][0]} if good else set()))
        selections.append(dict(minutes=int(minutes),eligible_count=len(good),selected_arm=good[0][0] if good else None,
                               family_nominations=family_choices,allowed_audit_arms=allowed,
                               eligible_validation_ranking=[dict(arm=a,net_pct=n,mdd_pct=d) for a,n,d in good]))
    result=dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),selection_only='development2023-24 plus validation2025',
                summary_sha256=sha(outdir/'summary.csv'),selections=selections,
                nomination_is_not_validation_or_deployment=True,
                no_gate_pass_means_no_approved_parameter=True,production_eligible=False)
    dump_json(outdir/'selection_lock.json',result)
    return result


def read_selection(folder, periods):
    path=Path(folder)/'selection_lock.json'
    lock=json.loads(path.read_text())
    if lock.get('selection_only')!='development2023-24 plus validation2025' or lock.get('summary_sha256')!=sha(Path(folder)/'summary.csv'):
        raise ValueError('Invalid or changed development selection evidence')
    by_period={int(x['minutes']):x for x in lock['selections']}
    if not set(periods).issubset(by_period): raise ValueError('No frozen selection for a requested timeframe')
    known={a['arm'] for a in ARMS}
    for m in periods:
        if not set(by_period[m]['allowed_audit_arms']).issubset(known): raise ValueError('Unknown arm in frozen selection')
    return by_period,sha(path)


def run(args):
    sources=committed_sources()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    folds=FOLDS[args.phase];cutoff=pd.Timestamp(folds[-1][2]) if str(folds[-1][2]).endswith('Z') else pd.Timestamp(folds[-1][2],tz='UTC')
    manifest=json.loads(Path(args.history).read_text())
    records=[r for r in manifest['symbols'] if r['status']=='complete']
    if sum(r['cohort']=='frozen_pool' for r in records) != 54:
        raise ValueError('The frozen 54-symbol pool is incomplete. Repair/verify the phase-specific history; do not select old samples using future tail quality.')
    if not records: raise ValueError('No validated history')
    periods=[int(x) for x in args.periods.split(',')]
    if any(x not in (15,60,240) for x in periods): raise ValueError('periods must be15/60/240')
    selections,selection_hash=read_selection(args.selection,periods) if args.phase=='audit' else ({},None)
    if args.derivatives and args.phase!='audit': raise ValueError('Derivatives are a separate short-history audit only')
    derivative_hashes={str(p.relative_to(Path(args.derivatives))):sha(p) for p in sorted(Path(args.derivatives).glob('normalized/*.csv'))} if args.derivatives else {}
    if args.derivatives:
        cp=Path(args.derivatives)/'coverage.json'
        coverage=json.loads(cp.read_text())
        if coverage['errors']: raise ValueError('Derivatives acquisition has unresolved errors')
        derivative_hashes['coverage.json']=sha(cp)
    exposure_round = args.exposure_round if args.phase == 'audit' else 0
    if args.phase == 'audit' and exposure_round < 1:
        raise ValueError('Audit exposure round must be positive')
    identity=dict(phase=args.phase,history_sha256=sha(Path(args.history)),periods=periods,sources=sources,derivatives=derivative_hashes,selection_sha256=selection_hash,folds=folds,exposure_round=exposure_round)
    state=out/'run_manifest.json'
    if state.exists():
        if json.loads(state.read_text())['identity']!=identity: raise ValueError('Output identity changed: use a new directory')
    else: dump_json(state,dict(identity=identity,created_at=pd.Timestamp.now(tz='UTC').isoformat(),formal_holdout_consumption=exposure_round,owner_authorized_all_dates=True))
    code_hash=sha(Path(__file__))[:12]
    cache=ROOT/'data/altcoin_trends_20260909_v1/feature_cache'/f'{args.phase}_{code_hash}'
    cache.mkdir(parents=True,exist_ok=True)
    base={m:{} for m in periods};paths={}; failures=[]
    for record in records:
        symbol=record['symbol']; raw=read_history(record,cutoff)
        for minutes in periods:
            bars=aggregate(raw,minutes)
            if len(bars)<COMMON_WARMUP+2: failures.append(dict(symbol=symbol,minutes=minutes,reason='insufficient_common_warmup'));continue
            paths[(symbol,minutes)]={}
            allowed=selections[minutes]['allowed_audit_arms'] if selections else [a['arm'] for a in ARMS]
            required_params={a['parameter'] for a in ARMS if a['arm'] in allowed}
            for name,param in PARAMS.items():
                # Only the base cache is needed for weekly ranks. Other arms
                # are calculated one at a time to bound disk/RAM on this Mac.
                if name!='base': continue
                path=cache/f'{symbol}_{minutes}_{name}.pkl.gz'
                if not path.exists():
                    f=build_altcoin_features(bars,param)
                    for col in bars: f[col]=bars[col]
                    temporary=path.with_suffix('.tmp')
                    f.to_pickle(temporary,compression='gzip')
                    temporary.replace(path)
                paths[(symbol,minutes)][name]=path
            b=pd.read_pickle(paths[(symbol,minutes)]['base'],compression='gzip')
            base[minutes][symbol]=b[['prior7d_atr_pct_mean','ready']]
        print(json.dumps(dict(stage='features',symbol=symbol)),flush=True)
    members={}
    for minutes in periods:
        members[minutes]=weekly_membership(base[minutes]);members[minutes].to_csv(out/f'weekly_membership_{minutes}.csv',index=False)
    dump_json(out/'unavailable.json',failures)
    results=[]
    for record in records:
        symbol=record['symbol']
        for minutes in periods:
            if (symbol,minutes) not in paths: continue
            if (out/f'{symbol}_{minutes}'/'diagnostics.json').exists():
                print(json.dumps(dict(stage='resume',symbol=symbol,minutes=minutes)),flush=True);continue
            results.append(evaluate_symbol(symbol,record,minutes,paths[(symbol,minutes)],members[minutes],folds,out,Path(args.derivatives) if args.derivatives else None,selections[minutes]['allowed_audit_arms'] if selections else None))
            print(json.dumps(dict(stage='evaluated',**results[-1])),flush=True)
    summary=summarize(out,records,periods,folds)
    if args.phase=='development': lock_selection(summary,out)
    dump_json(out/'completion.json',dict(completed_at=pd.Timestamp.now(tz='UTC').isoformat(),summary_sha256=sha(out/'summary.csv'),summary_rows=len(summary),unavailable=failures))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history',required=True)
    p.add_argument('--out-dir',required=True)
    p.add_argument('--phase',choices=FOLDS,required=True)
    p.add_argument('--periods',default='60,240')
    p.add_argument('--selection',default=str(EXPERIMENT/'results/development'))
    p.add_argument('--derivatives')
    p.add_argument('--exposure-round',type=int,default=1,help='Logged access round for audit; development records zero')
    run(p.parse_args())


if __name__=='__main__': main()
