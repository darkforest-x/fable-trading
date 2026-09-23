"""Report the preregistered V12.8 one-axis selective-entry experiment.

Uses only authenticated event ledgers. Outcomes never select the quintile gate.
UTC weeks are resampled jointly across baseline/treatment and across symbols;
matched random comparisons use net basis points, not differing risk denominators.
The high20 random pool is the single-feature baseline and primary comparator.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v128_recent_report import enrich, metrics, read_csv
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-expansion-entry-20260923-v1')
CONFIG = EXP / 'config.json'
TEST = Path('tests/evaluation/test_spike_v128_expansion_report.py')
TABLES = ('candidate_outcomes', 'serial_trades', 'serial_statuses', 'controls')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def boolean(series):
    return series.astype(str).str.lower().isin(['true', '1', '1.0'])


def normalize(frame, split):
    if frame.empty:
        return frame
    frame = frame.copy()
    for name in ['signal_close', 'entry_time', 'exit_time', 'control_signal_close', 'control_exit_time']:
        if name in frame:
            frame[name] = pd.to_datetime(frame[name], utc=True)
    for name in ['censored', 'matched', 'control_censored', 'high20']:
        if name in frame:
            frame[name] = boolean(frame[name])
    if 'net_return' in frame:
        # Non-fill candidates can have no outcome; keep them out of trade metrics.
        valid = frame.net_return.notna() & frame.exit_time.notna()
        if valid.all():
            frame = enrich(frame, split)
        else:
            frame = pd.concat([enrich(frame.loc[valid], split), frame.loc[~valid]], axis=0).sort_index()
    if 'signal_close' in frame:
        monday = frame.signal_close.dt.normalize() - pd.to_timedelta(frame.signal_close.dt.dayofweek, unit='D')
        frame['week'] = monday.dt.strftime('%Y-%m-%d')
    return frame


def period_rows(frame, period, split, *, mature=False):
    if frame.empty or period == 'all':
        return frame
    if period == 'later':
        return frame[frame.signal_close >= split]
    mask = frame.signal_close < split
    if mature:
        mask &= frame.exit_time < split
    return frame[mask]


def extended_metrics(frame):
    row = metrics(frame)
    for level in (3, 5, 10):
        n = int((frame.net_r >= level).sum()) if len(frame) else 0
        row[f'net_ge{level}r'] = n
        row[f'net_ge{level}r_rate'] = n / len(frame) if len(frame) else math.nan
    return row


def inference(values, blocks, seed, bootstrap):
    """Exact stable week sign flips for positive excess; cluster bootstrap interval."""
    a = pd.DataFrame({'v': values, 'week': blocks}).dropna()
    if not len(a):
        return {'matched': 0, 'weeks': 0, 'mean_excess_bp': math.nan, 'p_one_sided': math.nan,
                'ci_low_bp': math.nan, 'ci_high_bp': math.nan}
    g = a.groupby('week').v
    sums = np.array([math.fsum(x.tolist()) for _, x in g], float)
    counts = g.size().to_numpy(float)
    n = len(sums)
    result = {'matched': len(a), 'weeks': n, 'mean_excess_bp': math.fsum(a.v.tolist()) / len(a)}
    if n < 2:
        return result | {'p_one_sided': math.nan, 'ci_low_bp': math.nan, 'ci_high_bp': math.nan}
    rng = np.random.default_rng(seed)
    choices = rng.integers(0, n, (bootstrap, n))
    means = sums[choices].sum(axis=1) / counts[choices].sum(axis=1)
    observed = math.fsum(sums.tolist())
    if n <= 16:
        tally = sum(math.fsum(float(x)*s for x, s in zip(sums, signs)) >= observed
                    for signs in itertools.product((-1, 1), repeat=n))
        p, resolution = tally / (2**n), 1 / (2**n)
    else:
        signs = rng.choice([-1, 1], size=(10000, n))
        p = (1 + sum(math.fsum(float(x)*int(s) for x, s in zip(sums, row)) >= observed for row in signs)) / 10001
        resolution = 1 / 10001
    return result | {'p_one_sided': p, 'ci_low_bp': float(np.quantile(means, .025)),
                     'ci_high_bp': float(np.quantile(means, .975)), 'minimum_p_resolution': resolution}


def matched_rows(trades, controls, pool, period, split):
    """Join by frozen event identity; censor failures stay missing, never redrawn."""
    target = period_rows(trades, period, split, mature=True)
    target = target[~target.censored]
    c = controls[(controls.control_pool == pool) & controls.matched]
    if 'control_censored' in c and c.control_censored.any():
        raise ValueError('matched control cannot be censored')
    if not np.isfinite(c[['control_net_r','control_net_return']].to_numpy(float)).all():
        raise ValueError('matched control has a missing/nonfinite outcome')
    if c.trade_key.duplicated().any():
        raise ValueError('duplicate control key within pool')
    out = target.merge(c[['trade_key', 'control_net_r', 'control_net_return', 'control_exit_time']],
                       on='trade_key', how='inner', validate='one_to_one')
    if period == 'earlier':
        out = out[out.control_exit_time < split]
    return out


def control_metrics(trades, controls, pool, period, split, cfg):
    pair = matched_rows(trades, controls, pool, period, split)
    if pair.empty:
        return {'matched': 0, 'weeks': 0, 'mean_excess_bp': math.nan, 'p_one_sided': math.nan,
                'ci_low_bp': math.nan, 'ci_high_bp': math.nan}
    row = inference((pair.net_return-pair.control_net_return)*1e4, pair.week,
                    cfg['statistics_seed'], cfg['bootstrap'])
    return row | {'target_mean_net_bp': float(pair.net_bp.mean()),
                  'random_mean_net_bp': float(pair.control_net_return.mean()*1e4),
                  'target_mean_net_r': float(pair.net_r.mean()), 'random_mean_net_r': float(pair.control_net_r.mean()),
                  'target_win_rate': float((pair.net_return > 0).mean()),
                  'random_win_rate': float((pair.control_net_return > 0).mean())}


def holm(values):
    out = np.full(len(values), np.nan)
    valid = [(i, float(p)) for i, p in enumerate(values) if pd.notna(p)]
    running = 0.
    for rank, (i, p) in enumerate(sorted(valid, key=lambda x: x[1])):
        running = max(running, (len(values)-rank)*p)
        out[i] = min(1., running)
    return out


def comparison(base, treated, seed, bootstrap):
    """Resample the same calendar weeks in both policies, preserving shared trades."""
    cols = {'win_rate': lambda x: (x.net_return > 0).astype(float),
            'mean_net_r': lambda x: x.net_r, 'mean_net_bp': lambda x: x.net_bp,
            'net_ge5r_rate': lambda x: (x.net_r >= 5).astype(float),
            'net_ge10r_rate': lambda x: (x.net_r >= 10).astype(float)}
    weeks = sorted(set(base.week) | set(treated.week))
    if not len(base) or not len(treated) or len(weeks) < 2:
        return {'baseline_closed': len(base), 'high20_closed': len(treated), 'weeks': len(weeks)}
    rng = np.random.default_rng(seed); draws = rng.integers(0, len(weeks), (bootstrap, len(weeks)))
    out = {'baseline_closed': len(base), 'high20_closed': len(treated), 'weeks': len(weeks)}
    counts = [x.groupby('week').size().reindex(weeks, fill_value=0).to_numpy() for x in (base, treated)]
    den = [c[draws].sum(axis=1) for c in counts]
    valid = (den[0] > 0) & (den[1] > 0)
    out['valid_bootstrap_draws'] = int(valid.sum())
    for name, fn in cols.items():
        sums = [fn(x).groupby(x.week).sum().reindex(weeks, fill_value=0).to_numpy() for x in (base, treated)]
        delta = sums[1][draws].sum(axis=1)[valid]/den[1][valid] - sums[0][draws].sum(axis=1)[valid]/den[0][valid]
        out[f'delta_{name}'] = float(fn(treated).mean()-fn(base).mean())
        out[f'ci_low_{name}'] = float(np.quantile(delta, .025)) if len(delta) else math.nan
        out[f'ci_high_{name}'] = float(np.quantile(delta, .975)) if len(delta) else math.nan
    return out


def retention(base, treated, timeframe, arm, period):
    oldkeys, newkeys = set(base.trade_key), set(treated.trade_key)
    rows = []
    for level in (0, 3, 5, 10):
        old = base if level == 0 else base[base.net_r >= level]
        new = treated if level == 0 else treated[treated.net_r >= level]
        keep = int(old.trade_key.isin(newkeys).sum())
        rows.append({'timeframe_min': timeframe, 'arm': arm, 'period': period, 'threshold_net_r': level,
                     'baseline_count': len(old), 'retained': keep, 'lost': len(old)-keep,
                     'retention_rate': keep/len(old) if len(old) else math.nan,
                     'new_count': int((~new.trade_key.isin(oldkeys)).sum()), 'high20_total': len(new),
                     'baseline_high20_gate_passed': int((old.expansion_quintile == 5).sum())})
    return rows


def load_run(run):
    manifest = json.loads((run/'manifest.json').read_text())
    if not manifest.get('complete') or manifest.get('errors'):
        raise ValueError('report requires the complete error-free full run')
    identity = json.loads((run/'identity.json').read_text())
    cfg = json.loads(CONFIG.read_text()); parent = Path(cfg['parent_run'])
    pi = json.loads((parent/'identity.json').read_text())
    expected = {f'{s}_{m}m' for s in pi['symbols'] for m in cfg['timeframes']}
    if identity['config'] != cfg or identity['config_sha256'] != digest(CONFIG) or identity['subset']:
        raise ValueError('wrong study configuration or subset')
    if (identity['parent_identity_sha256'] != digest(parent/'identity.json') or
            identity['parent_manifest_sha256'] != digest(parent/'manifest.json')):
        raise ValueError('wrong parent identity')
    if (set(manifest['receipts']) != expected or identity['inputs'] != pi['inputs'] or
            identity['symbols'] != pi['symbols'] or identity['timeframes'] != cfg['timeframes']):
        raise ValueError('wrong study coverage')
    rid = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if manifest['run_identity'] != rid: raise ValueError('run identity digest mismatch')
    for path, sha in (pi['code'] | identity['code']).items():
        if digest(path) != sha: raise ValueError(f'source identity changed: {path}')
    if digest(pi['input_manifest']) != pi['input_manifest_sha256']:
        raise ValueError('parent input manifest changed')
    items = {name: [] for name in TABLES}; receipts = []
    for key, sha in manifest['receipts'].items():
        folder = run/'streams'/key
        if digest(folder/'receipt.json') != sha:
            raise ValueError(f'receipt changed {key}')
        rec = json.loads((folder/'receipt.json').read_text())
        if (rec['run_identity'] != rid or rec['input_sha256'] != identity['inputs'][rec['symbol']] or
                f"{rec['symbol']}_{rec['minutes']}m" != key or rec['status'] != 'complete' or
                not rec['summary']['parent_parity']): raise ValueError('invalid stream identity/parity')
        receipts.append(rec)
        for name in TABLES:
            file = name+'.csv.gz'
            if digest(folder/file) != rec['files'][file]:
                raise ValueError(f'ledger changed {key}/{file}')
            f = read_csv(folder/file)
            if len(f): items[name].append(f)
    return identity, receipts, {name: pd.concat(parts, ignore_index=True) if parts else pd.DataFrame() for name, parts in items.items()}


def run_report(run, output):
    if not _committed((Path(__file__), CONFIG, TEST)):
        raise ValueError('commit statistics builder, config and tests before constructing outputs')
    if output.exists(): raise ValueError('output exists; preserve previous reports')
    cfg = json.loads(CONFIG.read_text()); split = pd.Timestamp(cfg['split'])
    identity, receipts, frames = load_run(run)
    frames = {name: normalize(x, split) for name, x in frames.items()}
    trades, statuses, controls = (frames[k] for k in ('serial_trades','serial_statuses','controls'))
    if trades.duplicated(['policy','trade_key']).any(): raise ValueError('duplicate serial trade')
    counts, rows, comps, comparisons, tails, bins = [], [], [], [], [], []
    for (minutes, arm), whole in trades.groupby(['timeframe_min','arm']):
        c = controls[(controls.timeframe_min == minutes) & (controls.arm == arm)]
        s = statuses[(statuses.timeframe_min == minutes) & (statuses.arm == arm)]
        base, high = [whole[whole.policy == p] for p in ('baseline','high20')]
        common = base[['trade_key','net_r','net_return']].merge(high[['trade_key','net_r','net_return']],on='trade_key',suffixes=('_b','_h'))
        for name in ('net_r','net_return'):
            np.testing.assert_allclose(common[name+'_b'],common[name+'_h'],rtol=0,atol=1e-12)
        for period in ('all','earlier','later'):
            head={'timeframe_min':int(minutes),'arm':arm,'period':period}
            for policy, t in (('baseline',base),('high20',high)):
                signal_cohort=period_rows(t,period,split)
                p=period_rows(t,period,split,mature=True); closed=p[~p.censored]
                st=period_rows(s[s.policy==policy],period,split)
                entry_count=len(signal_cohort)
                row=head|{'policy':policy,'candidates':len(st),'taken':entry_count,
                          'censored':int(signal_cohort.censored.sum()),
                          'crossing_excluded_closed':int(((signal_cohort.exit_time>=split)&~signal_cohort.censored).sum()) if period=='earlier' else 0}
                for name in ('skipped_in_position','filtered_unknown','filtered_quintile'):
                    row[name]=int((st.status==name).sum())
                rows.append(row|extended_metrics(closed))
                for pool in ('all','high20') if policy=='high20' else ('all',):
                    comps.append(head|{'policy':policy,'control_pool':pool}|control_metrics(t,c,pool,period,split,cfg))
            b=period_rows(base,period,split,mature=True); b=b[~b.censored]
            h=period_rows(high,period,split,mature=True); h=h[~h.censored]
            comparisons.append(head|comparison(b,h,cfg['statistics_seed'],cfg['bootstrap']))
            tails.extend(retention(b,h,int(minutes),arm,period))
            for q, subset in b.groupby('expansion_quintile'):
                bins.append(head|{'quintile':int(q)}|extended_metrics(subset)|
                            {('control_'+k):v for k,v in control_metrics(subset,c,'all',period,split,cfg).items()})
    output.mkdir(parents=True)
    tables={'metrics':pd.DataFrame(rows),'controls_summary':pd.DataFrame(comps),'policy_comparison':pd.DataFrame(comparisons),
            'retention':pd.DataFrame(tails),'baseline_quintiles':pd.DataFrame(bins)}
    controls_summary=tables['controls_summary'];controls_summary['holm_primary_p']=np.nan
    main=(controls_summary.period=='all')&(controls_summary.policy=='high20')&(controls_summary.control_pool=='high20')
    assert main.sum()==4
    controls_summary.loc[main,'holm_primary_p']=holm(controls_summary.loc[main,'p_one_sided'].tolist())
    direction_rows, cross_rows = [], []
    for (minutes, arm, policy, side), t in trades.groupby(['timeframe_min','arm','policy','side']):
        c = controls[(controls.timeframe_min == minutes) & (controls.arm == arm) & (controls.side == side)]
        for period in ('all','earlier','later'):
            mature = period_rows(t, period, split, mature=True)
            closed = mature[~mature.censored]
            head = {'timeframe_min': minutes, 'arm': arm, 'policy': policy, 'side': side, 'period': period}
            row = head | extended_metrics(closed)
            for pool in ('all','high20') if policy == 'high20' else ('all',):
                row.update({pool+'_'+k:v for k,v in control_metrics(t,c,pool,period,split,cfg).items()})
            direction_rows.append(row)
        cross = t[(t.signal_close < split) & (t.exit_time >= split) & ~t.censored]
        row = {'timeframe_min': minutes, 'arm': arm, 'policy': policy, 'side': side} | extended_metrics(cross)
        row.update({'all_'+k:v for k,v in control_metrics(cross,c,'all','all',split,cfg).items()})
        cross_rows.append(row)
    tables['direction'] = pd.DataFrame(direction_rows)
    tables['cross_split'] = pd.DataFrame(cross_rows)
    fixed_rows = []
    candidates = frames['candidate_outcomes']
    for (minutes,arm), whole in candidates.groupby(['timeframe_min','arm']):
        c = controls[(controls.timeframe_min == minutes) & (controls.arm == arm)]
        for policy in ('baseline','high20'):
            cohort = whole if policy == 'baseline' else whole[whole.high20]
            for period in ('all','earlier','later'):
                mature = period_rows(cohort,period,split,mature=True)
                closed = mature[mature.exit_i.notna() & ~mature.censored.fillna(True)]
                head = {'timeframe_min':minutes,'arm':arm,'policy':policy,'period':period,
                        'candidates':len(period_rows(cohort,period,split))}
                row = head | extended_metrics(closed)
                for pool in ('all','high20') if policy == 'high20' else ('all',):
                    row |= {pool+'_'+k:v for k,v in control_metrics(closed,c,pool,period,split,cfg).items()}
                fixed_rows.append(row)
    tables['fixed_candidates'] = pd.DataFrame(fixed_rows)
    tables['control_draw_status'] = controls.groupby(['timeframe_min','arm','control_pool','reason']).size().reset_index(name='draws')
    for name, table in tables.items(): table.to_csv(output/(name+'.csv'),index=False)
    statuses.groupby(['timeframe_min','arm','policy','status']).size().reset_index(name='n').to_csv(output/'status_counts.csv',index=False)
    coverage=[]
    for r in receipts:
        row={'symbol':r['symbol'],'timeframe_min':r.get('minutes',r.get('timeframe_min'))}
        row.update(r.get('summary',{}));coverage.append(row)
    pd.DataFrame(coverage).to_csv(output/'coverage.csv',index=False)
    trades.to_csv(output/'serial_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    frames['candidate_outcomes'].to_csv(output/'candidate_outcomes.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    controls.to_csv(output/'controls.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    plot_summary(tables['metrics'],output/'outcomes.png')
    receipt={'generated_at':pd.Timestamp.now(tz='UTC').isoformat(),'input_run':str(run),
             'run_manifest_sha256':digest(run/'manifest.json'),'run_identity_sha256':digest(run/'identity.json'),
             'stream_count':len(receipts),'builder_sha256':digest(__file__),
             'files':{p.name:digest(p) for p in sorted(output.iterdir())}}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'output':str(output),'streams':len(receipts),'serial_rows':len(trades),'files':len(receipt['files'])}),flush=True)


def plot_summary(table,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t=table[table.period=='all'];fig,axes=plt.subplots(1,3,figsize=(13,4.4))
    keys=[(15,'v9_both'),(15,'joint'),(60,'v9_both'),(60,'joint')]
    labels=['15m SPIKE','15m Joint','1h SPIKE','1h Joint'];x=np.arange(4)
    for ax,metric,title,scale in zip(axes,['taken','win_rate','net_ge5r_rate'],['Entries','Net win rate (%)','Realized net >=5R (%)'],[1,100,100]):
        for j,policy in enumerate(['baseline','high20']):
            vals=[float(t[(t.timeframe_min==m)&(t.arm==a)&(t.policy==policy)].iloc[0][metric])*scale for m,a in keys]
            ax.bar(x+(j-.5)*.36,vals,width=.36,label=policy,color=['#8d99ae','#087f8c'][j])
        ax.set_title(title);ax.set_xticks(x,labels,rotation=18);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    axes[0].legend();fig.suptitle('SPIKE V12.8 | Causal relative-volatility upper-quintile entry gate')
    fig.tight_layout();fig.savefig(path,dpi=160);plt.close(fig)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();run_report(args.run,args.output)
