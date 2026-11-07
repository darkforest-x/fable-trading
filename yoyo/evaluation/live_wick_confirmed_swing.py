"""Landmark study on saved live-wick E20 events and their original native exit paths."""
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.monday_limit_close_reclaim import committed
from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.trendline_v2_signals import confirmed_pivot_highs

EXP = ROOT/'experiments/active/exp-live-wick-confirmed-swing-20261107-v1'
STEP = pd.Timedelta(minutes=5)


def pivots_before(frame, end, side):
    """Use high (long) or negated low (short) on the contiguous range strictly before entry close; five bars each side."""
    count = 96
    while True:
        start = max(0, end-count)
        pre = frame.iloc[start:end]
        gaps = np.flatnonzero(np.diff(pre.index.asi8) != STEP.value)
        if len(gaps):
            pre = pre.iloc[gaps[-1]+1:]
        v = pre.high.to_numpy() if side == 1 else -pre.low.to_numpy()
        strict, _ = confirmed_pivot_highs(v, 5, 5)
        centres = strict[strict >= 0]
        if len(centres):
            w = np.lib.stride_tricks.sliding_window_view(v, 11)
            rightmost = np.flatnonzero((w[:, 5] >= w[:, :5].max(axis=1)) & (w[:, 5] > w[:, 6:].max(axis=1))) + 5
            return [(pre.index[i], pre.index[i+5], side*v[i]) for i in (centres[-1], rightmost[-1])]
        if start == 0 or len(gaps):
            return None
        count *= 2


def shape(frame, entry, due, side, target, week):
    """At checkpoint inspect only closed 5m prices from the entry through due. Monday bounds and reference highs/lows are past data."""
    a, b = frame.index.get_indexer([entry, due])
    if min(a,b) < 0 or not frame.index[a:b+1].equals(pd.date_range(entry, due, freq=STEP)):
        raise ValueError('Missing five-minute path to confirmation')
    monday = pd.Timestamp(week, tz='America/New_York')
    start = monday.tz_convert('UTC')
    end = (monday+pd.DateOffset(days=1)).tz_convert('UTC')
    grid = pd.date_range(start+STEP, end, freq=STEP)
    m = frame.reindex(grid)
    if m[['high','low']].isna().any().any():
        raise ValueError('Incomplete NY Monday')
    high, low = m.high.max(), m.low.min()
    edge = low if side == 1 else high
    level = edge-side*.618*(high-low)
    if not np.isclose(edge, target, rtol=1e-6, atol=1e-12):
        raise ValueError('Target disagrees with archived Monday boundary')
    out = dict(confirm_close=frame.close.iloc[b], level=level, observed_entry=frame.close.iloc[a])
    points = pivots_before(frame, a, side)
    if points is None:
        return out
    path = side*frame.close.iloc[a:b+1].to_numpy()
    for (p, confirmed, price), suffix in zip(points, ('','_plateau')):
        crosses = np.flatnonzero((path[:-1] <= side*price) & (path[1:] > side*price))
        out.update({f'pivot{suffix}':price, f'pivot_bar{suffix}':p, f'pivot_confirmed{suffix}':confirmed,
                    f'breakout{suffix}':bool(len(crosses))})
        if len(crosses):
            out[f'first_break{suffix}'] = frame.index[a+crosses[0]+1]
    out['pivot_before_target'] = side*(target-out['pivot']) > 0
    return out


def fills(root, symbol, path):
    file = root/path/'symbols'/symbol/'trades.csv.gz'
    if not file.exists():
        return pd.DataFrame()
    f = pd.read_csv(file)
    f = f[f.variant.eq('E20') & f.trade_id.str.contains(':near_monday_edge:', regex=False)].copy()
    f['event'] = f.trade_id.str.split(':').str[:3].str.join(':')
    if f.event.duplicated().any():
        raise ValueError('Duplicate symbol/path event')
    f['net'] = f.net_notional_bp-10.-np.where(f.reason == 4, 0., 10.)
    f['exit'] = pd.to_datetime(f.exit_time, utc=True)
    return f.set_index('event')


def symbol_rows(args):
    symbol, candidates, cfg = args
    ext = ROOT/cfg['extension']
    sources = {}
    for name, directory in [('base', ROOT/cfg['fills']), ('extension', ext)]:
        sources[name] = [fills(directory, symbol, path) for path in ('ohlc','olhc')]
    rows = []
    for event, side, entry_t, saved_net, far, funded in candidates:
        src = 'base' if event in sources['base'][0].index else 'extension'
        o, l = [z.loc[event] for z in sources[src]]
        pick = l if l.net < o.net else o
        if abs(pick.net-saved_net) > 1e-7 or o.side != side or l.side != side:
            raise ValueError(f'{event}: native net / side parity failed')
        due = (entry_t-STEP).floor('h') + pd.Timedelta(hours=2)
        alive = lambda z: z.reclaim_status == 'reclaimed' and z.exit > due
        if pick.exit > due and pick.reclaim_status != 'reclaimed':
            raise ValueError(f'{event}: non-reclaimed trade after deadline')
        rows.append(dict(event=event, symbol=symbol, side=side, entry_t=entry_t, week=event.split(':')[1],
                         due=due, entry=pick.entry, target=pick.target_price, exit_price=pick.exit_price,
                         exit_time=pick.exit, reason=int(pick.reason), status=pick.reclaim_status, net_bp=pick.net,
                         eligible=alive(pick), alive_ohlc=alive(o), alive_olhc=alive(l), reason_ohlc=int(o.reason),
                         reason_olhc=int(l.reason), far=far, funded_6x3=funded, source=src))
    eligible = [r for r in rows if r['eligible']]
    if eligible:
        job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
        if job['execution_minutes'] != 5:
            raise ValueError('Unexpected source interval')
        if any(r['source']=='extension' for r in eligible):
            job = json.loads((ext/'jobs.json').read_text())[symbol]
        frame, _ = load_job(job)
        frame.index = frame.index+STEP
        for r in eligible:
            r.update(shape(frame, r['entry_t'], r['due'], r['side'], r['target'], r['week']))
            if not np.isclose(r['observed_entry'], r['entry'], rtol=1e-6, atol=1e-12) or r['side']*(r['confirm_close']-r['level']) <= 0:
                raise ValueError(f"{r['event']}: clock or reclaim level mismatch")
            gross_to_due = 10000*r['side']*(r['confirm_close']-r['entry'])/r['entry']
            r['remaining_net_bp'] = r['net_bp']-gross_to_due+15.
    return rows


def stats(g):
    if not len(g):
        return {'n':0}
    return dict(n=len(g), weeks=int(g.week.nunique()), target=int(g.reason.eq(4).sum()),
                protective_stop=int(g.reason.eq(5).sum()), reasons={str(k):int(n) for k,n in g.reason.value_counts().items()},
                mean_full_net_bp=float(g.net_bp.mean()), median_full_net_bp=float(g.net_bp.median()),
                mean_remaining_net_bp=float(g.remaining_net_bp.mean()), median_remaining_net_bp=float(g.remaining_net_bp.median()))


def contrast(x, cfg):
    """Uncertainty for the landmark association; resample whole NY weeks and shuffle labels within pre-specified strata."""
    if x.breakout.nunique() < 2:
        return {'estimable':False}
    x = x.reset_index(drop=True)
    labels = x.breakout.to_numpy(bool)
    metric = np.c_[x.reason.eq(4).astype(float)*100, x.remaining_net_bp]
    def gap(idx, group):
        return metric[idx[group]].mean(axis=0)-metric[idx[~group]].mean(axis=0)
    pos = np.arange(len(x))
    rng = np.random.default_rng(cfg['seed'])
    obs = gap(pos, labels)
    weeks = list(x.groupby('week').indices.values())
    strata = list(x.groupby([x.entry_t.dt.year, x.side, x.pivot_before_target]).indices.values())
    boot, null = [], []
    for _ in range(cfg['draws']):
        idx = np.concatenate([weeks[i] for i in rng.integers(0,len(weeks),len(weeks))])
        mask = labels[idx]
        if mask.any() and not mask.all():
            boot.append(gap(idx,mask))
        shuffled = labels.copy()
        for members in strata:
            shuffled[members] = rng.permutation(labels[members])
        null.append(gap(pos,shuffled))
    return dict(estimable=True, metrics=['target_rate_pp','remaining_net_bp'], observed=obs.tolist(),
                week_cluster_ci95=np.percentile(boot,[2.5,97.5],axis=0).tolist() if boot else None,
                bootstrap_valid=len(boot), movable_cases=sum(len(m) for m in strata if labels[m].any() and not labels[m].all()),
                permutation_one_sided_p=((1+(np.array(null)>=obs-1e-10).sum(axis=0))/(len(null)+1)).tolist())


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    committed('yoyo/evaluation/live_wick_confirmed_swing.py', str((EXP/'config.json').relative_to(ROOT)))
    raw = pd.read_csv(ROOT/cfg['population'], parse_dates=['entry_t'])
    original = raw[(raw.start == '2020-01-01') & (raw.account == 'live-wick6x3')].copy()
    far = raw[(raw.start == '2020-01-01') & (raw.account == 'live-wick3x3far')]
    base = ROOT/cfg['base']
    ledger = pd.read_csv(base.parent/'near_monday_edge-2020-ledger.csv.gz')
    funded = ledger[ledger.notional > 0].set_index('trade_id')
    original['far'] = original.event.isin(far.event)
    original['funded'] = original.event.isin(funded.index)
    jobs = [(s,list(g[['event','side','entry_t','net','far','funded']].itertuples(index=False,name=None)),
             json.loads(base.read_text())) for s,g in original.groupby('symbol')]
    with ProcessPoolExecutor(5) as ex:
        rows = pd.DataFrame([r for block in ex.map(symbol_rows,jobs) for r in block])
    if len(rows)!=416 or rows.event.duplicated().any() or rows.funded_6x3.sum()!=len(funded):
        raise ValueError('Frozen event coverage mismatch')
    check = rows[rows.funded_6x3].set_index('event').loc[funded.index]
    if not np.array_equal(check.reason, funded.reason) or not np.allclose(check.net_bp, funded.net_bp, atol=1e-7,rtol=0):
        raise ValueError('Funded original ledger differs')
    x = rows[rows.eligible & rows.breakout.notna()].copy()
    masks = {'all':np.ones(len(x),bool),'through_2024':x.entry_t.dt.year<=2024,'2025':x.entry_t.dt.year==2025,
             '2026':x.entry_t.dt.year==2026,'long':x.side==1,'short':x.side==-1,
             'far':x.far,'funded_6x3':x.funded_6x3,'pivot_before_target':x.pivot_before_target.eq(True)}
    groups = {k:{'break':stats(x[m & x.breakout.eq(True)]),'no_break':stats(x[m & x.breakout.eq(False)])} for k,m in masks.items()}
    rows['exit_clock'] = np.select([rows.exit_time<rows.due,rows.exit_time==rows.due],['before','at'],default='after')
    flow = rows.groupby(['exit_clock','reason','status']).size()
    rows.sort_values(['entry_t','symbol']).to_csv(EXP/'events.csv.gz', index=False)
    dump(EXP/'summary.json',dict(events=len(rows), before=int((rows.exit_clock=='before').sum()), at=int((rows.exit_clock=='at').sum()),
         after=int((rows.exit_clock=='after').sum()), eligible=int(rows.eligible.sum()), classified=len(x),
         path_eligibility_disagreements=int((rows.alive_ohlc!=rows.alive_olhc).sum()),
         path_future_reason_disagreements=int((x.reason_ohlc!=x.reason_olhc).sum()),
         flow={'|'.join(map(str,key)):int(n) for key,n in flow.items()},groups=groups,contrast=contrast(x,cfg),
         plateau_changed=int((x.breakout!=x.breakout_plateau).sum()), plateau_break=stats(x[x.breakout_plateau.eq(True)]),
         plateau_no_break=stats(x[x.breakout_plateau.eq(False)]),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))


if __name__ == '__main__':
    run()
