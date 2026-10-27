"""Audit: the frozen Monday1618 entry score reads the touch bar itself (owner 2026-10-27).

Found while building the pre-scored arming replay (monday_limit_prescore_arm): monday_limit_reclaim_patterns3
computes vol_ratio, liquidity and toward24 at the ledger's entry_i, documented as "the submission bar (the bar
before the touch)", but in the Codex ledgers entry_i equals touch_i_hint, the bar whose low/high reaches the limit.
The fill happens inside that bar, so its close and full-bar volume are not known at the entry decision. bucket,
approach, Monday close side, range, side, cluster24 and opp_first are unaffected.

This recomputes the three features on the bar before the touch with the same formulas, scores both versions with the
frozen model (exp-monday-limit-reclaim-recent-20261025-v1/score_model.json, unchanged) and compares, on the same
outcomes: the v2 near-edge 60m net per event (events_60m) and the v3_be0 legs of exp-monday-limit-be-scalein (worse
path, 10bp slippage, as monday_limit_return_opt). The touch-bar recomputation must reproduce the stored scores.
Read-only; no refit, no new fills. Events whose causal score clears 0.5 but whose stored score does not have no v3
fills, so a corrected v3 needs a causal refit and a framework rerun.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SNAP
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, RUN, clean
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-score-leak-audit-20261027-v1'
LCOLS = ['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'touch_i_hint', 'entry_i']


def bar_features(symbol):
    """vol_ratio, liquidity and toward24 at the ledger entry_i (stored) and at the bar before the touch (causal)."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return pd.DataFrame()
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=LCOLS)
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
    if t.empty:
        return pd.DataFrame()
    f, _ = load_job(job)
    c, v = f.close.to_numpy(), f.volume.to_numpy(); qv = v*c
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    rows = []
    for r in t.itertuples():
        k = int(r.touch_i_hint)
        out = dict(event=f'{symbol}:{r.week}:{int(r.side):+d}', lag=int(r.entry_i)-k)
        for tag, s in (('ledger', int(r.entry_i)), ('causal', k-1)):
            out[f'vol_ratio_{tag}'] = hour[s]/base[s] if base[s] > 0 else np.nan
            out[f'liquidity_{tag}'] = day[s]
            out[f'toward24_{tag}'] = -r.side*100*(c[s]/c[s-288]-1) if s >= 288 else np.nan
        rows.append(out)
    return pd.DataFrame(rows)


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_score_leak_audit.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    spec = json.loads((ROOT/cfg['score_model']).read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(cfg['workers']) as ex:
        x = pd.concat([d for d in ex.map(bar_features, syms, chunksize=4) if len(d)], ignore_index=True)
    ev = pd.read_csv(ROOT/cfg['events'])
    ev['event'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')
    v2 = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(cfg['workers']) as ex:
        extra = pd.concat(list(ex.map(opt.ledger_extra, v2, chunksize=8)), ignore_index=True)
    extra['event'] = extra.trade_id.str.split(':').str[:3].str.join(':')
    s = ev.set_index('event').join(extra.set_index('event')[['bucket', 'range_pct']], how='inner').join(x.set_index('event'), how='inner')
    s['side'] = s.side_n.astype(float); s['log_approach'] = np.log1p(s.approach_min.clip(lower=0))
    s['opp_first'] = (s.opp_first == '是').astype(int)
    sc = {}
    for tag in ('ledger', 'causal'):
        g = s.copy()
        g['log_vol_ratio'] = np.log(g[f'vol_ratio_{tag}'].clip(lower=.05)); g['log_liquidity'] = np.log10(g[f'liquidity_{tag}'].clip(lower=1e4))
        g['toward24'] = g[f'toward24_{tag}']
        X = g[spec['features']].to_numpy(float)
        z = ((X-np.array(spec['mean']))/np.array(spec['scale'])*np.array(spec['coef'])).sum(1)+spec['intercept']
        sc[tag] = pd.Series(1/(1+np.exp(-z)), index=g.index)
    out = pd.DataFrame(sc).join(s[['lag', 'net_bp', 't']])
    out = out.join(pd.read_csv(ROOT/cfg['scores']).set_index('event').score.rename('stored'), how='left')
    out['t'] = pd.to_datetime(out.t, utc=True)
    out[['stored', 'ledger', 'causal']].reset_index().to_csv(exp/'scores_causal.csv.gz', index=False)
    tn = pd.read_csv(ROOT/cfg['trades'], usecols=['event', 'variant'])
    events = set(tn[tn.variant == 'v3_be17'].event)-BAD_EVENTS
    with ProcessPoolExecutor(cfg['workers']) as ex:
        legs = pd.concat([d for d in ex.map(so.legs, [(e, {RUN.name: ['v3_be0']}, events) for e in sorted({e.split(':')[0] for e in events})],
                                            chunksize=4) if len(d)], ignore_index=True).set_index('event')
    legs = legs.join(out[['causal']], how='left')
    stat = lambda g, col, win: dict(n=int(len(g)), mean_bp=float(g[col].mean()) if len(g) else None, win_pct=float(100*(g[col] > win).mean()) if len(g) else None)
    res = dict(events=int(len(out)), entry_i_minus_touch=dict(out.lag.value_counts().astype(int)),
               ledger_vs_stored_max_abs=float((out.ledger-out.stored).abs().max()),
               causal_vs_stored_abs_q={f'q{int(100*q)}': float((out.causal-out.stored).abs().quantile(q)) for q in (.5, .9, .99)},
               corr=float(out[['stored', 'causal']].corr().iloc[0, 1]), bands={}, v2_near_edge={}, v3_be0={}, by_period={})
    for cut in cfg['cuts']:
        a, b = out.stored >= cut, out.causal >= cut
        res['bands'][str(cut)] = dict(stored=int(a.sum()), causal=int(b.sum()), both=int((a & b).sum()))
    a, b = out.stored >= .5, out.causal >= .5
    o = out[out.net_bp.notna()]
    for name, m in (('all', o.index == o.index), ('stored>=0.5', a.reindex(o.index)), ('causal>=0.5', b.reindex(o.index)),
                    ('stored_only', (a & ~b).reindex(o.index)), ('causal_only', (b & ~a).reindex(o.index)), ('both', (a & b).reindex(o.index))):
        res['v2_near_edge'][name] = stat(o[m], 'net_bp', 0)
    for name, m in (('all', legs.index == legs.index), ('causal>=0.5', legs.causal >= .5), ('causal<0.5', legs.causal < .5)):
        res['v3_be0'][name] = stat(legs[m], 'net', 1)
    for name, lo, hi in (('2020_2024', None, '2025-01-01'), ('from_2025', '2025-01-01', None)):
        m = o.t.notna()
        if lo: m &= o.t >= pd.Timestamp(lo, tz='UTC')
        if hi: m &= o.t < pd.Timestamp(hi, tz='UTC')
        res['by_period'][name] = {k: stat(o[m & (o[k] >= .5)], 'net_bp', 0) for k in ('stored', 'causal')}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
