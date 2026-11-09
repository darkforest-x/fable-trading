"""Choose the lifetime of the 2R order from contemporaneous cross-asset return dispersion."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_weight_trim as tested

EXP = tested.EXP.parent / 'exp-cloud-close-dispersion-expiry-20261109-v1'


def dispersion_panel():
    """Use only complete 12H closes available before the key opens, after 90 days of continuity."""
    coverage = pd.read_csv(tested.w.SOURCE/'input_coverage.csv',keep_default_na=False)
    columns = {}
    for row in coverage.itertuples():
        source = tested.w.TOP if row.cohort=='top20_reused' else tested.w.SOURCE
        bars = pd.read_csv(source/'inputs'/row.symbol/'bars12h.csv.gz',usecols=['time','close'])
        available = leader.trailing_return(bars).notna()
        latest = np.log(bars.close).diff()
        columns[row.symbol] = pd.Series(latest.to_numpy(), index=available.index).where(available)
    panel = pd.DataFrame(columns).sort_index()
    counts = panel.count(axis=1)
    dispersion = (panel.quantile(.75,axis=1)-panel.quantile(.25,axis=1)).where(counts.ge(10))
    prior = dispersion.shift().rolling(20,min_periods=20).median()
    prior = prior.where(prior.index.to_series().diff(20).eq(20*leader.BAR))
    return pd.DataFrame(dict(eligible_coins=counts,iqr=dispersion,previous_median=prior,
                             wide=dispersion.gt(prior),available=dispersion.notna() & prior.notna()))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed dispersion study')
    panel = dispersion_panel()
    panel.to_csv(EXP/'market_clocks.csv.gz',index_label='entry_ms')
    trend = tested.expiry.trend
    paths = trend.thirds.okx_paths()
    paths = paths[~paths.is_control]
    expiring,_ = tested.expiry.time_in_force(paths)
    wide_keys = set(paths.loc[paths.entry_ms.isin(panel.index[panel.wide]),'event_id'])
    mixed = pd.concat([paths[~paths.event_id.isin(wide_keys)],expiring[expiring.event_id.isin(wide_keys)]],ignore_index=True)
    ref = pd.read_csv(tested.EXP/'funded_OKX.csv.gz').set_index('event_id')
    summary = dict(frozen_always_expire=json.loads((tested.EXP/'summary.json').read_text())['OKX']['metrics'])
    for label,rows in [('always_rest',paths),('wide_market_expiry',mixed)]:
        pos_path,eq_path,trim_path = (EXP/f'funded_{label}.csv.gz', EXP/f'hourly_{label}.csv.gz', EXP/f'trim_orders_{label}.csv.gz')
        if all(p.is_file() for p in (pos_path,eq_path,trim_path)):
            pos = pd.read_csv(pos_path).set_index('event_id')
            eq = pd.read_csv(eq_path,index_col='hour_end_utc',parse_dates=True)
            trims = pd.read_csv(trim_path)
            metrics = tested.summarize(pos,eq)
        else:
            data = tested.admin.study.tenth.split.pair_data(rows[rows.tp_r.eq(0)],rows[rows.tp_r.eq(2)],9)
            metrics,pos,eq,trims = tested.trim_account(data,tested.w.quotes,1.,.2)
            pos.to_csv(pos_path)
            eq.to_csv(eq_path,index_label='hour_end_utc')
            trims.to_csv(trim_path,index=False)
        if set(pos.index)!=set(ref.index):
            raise ValueError('Changing an exit fraction before runner close must not change the funded parent set')
        if not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],atol=.005,rtol=1e-8):
            raise ValueError('Funded cash and marked wealth differ')
        summary[label] = dict(metrics=metrics,trimmed=trims.event_id.nunique(),
                              wide_events=len(set(pos.index)&wide_keys),
                              missing_regime_fallback=int((~pos.entry_ms.isin(panel.index[panel.available])).sum()))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
