"""Cross-sectional 90-day leadership computed before the V1.7 key candle opens."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_priority as priority
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_slots as capacity


EXP = wallet.ROOT/'experiments/active/exp-cloud-close-leadership-20261109-v1'
BAR = 12*wallet.HOUR


def trailing_return(bars):
    """Use consecutive 12h closes [t-180,t]; signal boundary is open(t)+24h."""
    returns = bars.close / bars.close.shift(180) - 1
    contiguous = bars.time - bars.time.shift(180) == 180*BAR
    if not bars.time.is_monotonic_increasing or not bars.time.is_unique or (bars.time%BAR).any():
        raise ValueError('Invalid native 12h clock')
    if bars.close.le(0).any() or not np.isfinite(bars.close).all():
        raise ValueError('Invalid native 12h close')
    return pd.Series(returns.where(contiguous).to_numpy(),index=bars.time+2*BAR)


def leadership():
    coverage = pd.read_csv(wallet.SOURCE/'input_coverage.csv',keep_default_na=False)
    history = {}
    for row in coverage.itertuples():
        parent = wallet.TOP if row.cohort=='top20_reused' else wallet.SOURCE
        file = parent/'inputs'/row.symbol/'bars12h.csv.gz'
        bars = pd.read_csv(file,usecols=['time','close'])
        history[row.symbol] = trailing_return(bars)
    if len(history)!=291 or len(history)!=len(coverage):
        raise ValueError('Unexpected universe count')
    wide = pd.DataFrame(history)
    members = wide.notna().sum(axis=1)
    rank = (wide.rank(axis=1,ascending=False,method='average')-.5).div(members,axis=0)
    return rank.loc[members.ge(10)].rename_axis(index='entry_ms',columns='symbol').stack()


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    parent = json.loads((priority.EXP/'summary.json').read_text())
    slots, fraction, rule = parent['slots'],parent['parent_fraction'],parent['pick']
    if (slots,fraction,rule)!=(9,.75,'low_prior_atr'):
        raise ValueError('Unexpected development parent')
    paths = wallet.read_paths()
    paths = paths[paths.tp_r.isin([0,2])].copy()
    scores = leadership()
    asc = scores.reindex(pd.MultiIndex.from_frame(paths[['entry_ms','symbol']])).to_numpy()
    paths['leadership_rank'] = np.where(paths.side.eq(1),asc,1.-asc)
    source = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    source.index = pd.RangeIndex(1,len(source)+1)
    paths['entry_order'] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    base = pd.read_csv(priority.EXP/'account_metrics.csv')
    rows = base[base.policy.eq(rule)].drop(columns='policy').assign(top_fraction=-1.).to_dict('records')
    curves, counts = {},{}
    def pool(f):
        part = paths[paths.leadership_rank.le(f)]
        real = part[~part.is_control]
        controls = part[part.is_control&part.parent_id.isin(real.parent_id)]
        return real,controls
    for f in cfg['top_fraction']:
        real,ctl = pool(f)
        counts[f] = dict(eligible_real=len(real)//2,eligible_paired=len(ctl)//2)
        m,trades,eq = capacity.run(real,slots,fraction)
        rows.extend(dict(top_fraction=f,period=p,**v) for p,v in m.items())
        curves[f] = trades,eq
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick = float(eligible.top_fraction.iloc[0]) if len(eligible) else -1.
    best = float(dev.top_fraction.iloc[0])
    checks = {}
    for f in sorted({pick,best}-{-1.}):
        trades,eq = curves[f]
        trades.to_csv(EXP/f'funded_{f}.csv.gz')
        eq.to_csv(EXP/f'hourly_{f}.csv.gz',index_label='hour_end_utc')
        real,ctl = pool(f)
        checks[f] = {}
        for name,events,slip in [('control',ctl,0.),('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('slippage_5bp',real,.0005)]:
            checks[f][name] = capacity.run(events,slots,fraction,slip)[0]
            gc.collect()
    res = dict(pick=pick,best_dev_growth=best,eligible=eligible.top_fraction.tolist(),coverage=counts,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(res,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
