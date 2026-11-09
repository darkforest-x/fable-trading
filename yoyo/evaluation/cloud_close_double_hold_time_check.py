"""Paired calendar evidence for holding the original V1.7 long runner after a doubling on two venues."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]/'experiments/active'
EXP = ROOT/'exp-cloud-close-double-hold-time-check-20261109-v1'


def read(path):
    frame = pd.read_csv(path,usecols=['hour_end_utc','nav'],parse_dates=['hour_end_utc']).set_index('hour_end_utc')
    if (not frame.index.is_unique or not frame.index.is_monotonic_increasing or
            frame.index.to_series().diff().dropna().ne(pd.Timedelta(hours=1)).any() or frame.nav.le(0).any()):
        raise ValueError('Nonpositive NAV, duplicated hour, or missing paired clock')
    return frame.nav


def compare(name,base,runner):
    a,b = read(base),read(runner)
    if not a.index.equals(b.index):
        raise ValueError('Within-venue hourly account clocks differ')
    pair = pd.DataFrame({'base':a,'runner':b})
    # An observation labelled 00:00 is the close of the previous trading day.
    days = pair.groupby((pair.index-pd.Timedelta(nanoseconds=1)).floor('D')).last()
    logs = np.log(days/days.shift(1).fillna(1000.))
    delta = logs.runner-logs.base
    gross = np.exp(delta.sum())
    if not np.isclose(gross,b.iat[-1]/a.iat[-1],rtol=1e-11):
        raise ValueError('Paired daily log wealth does not telescope to settled capital')
    annual = delta.groupby(delta.index.year).sum()
    quarters = delta.groupby(delta.index.to_period('Q')).sum()
    values = delta.to_numpy()
    rng = np.random.default_rng(1109)
    length,draws = 90,5000
    prefix = np.r_[0.,values.cumsum()]
    # Fixed length moving blocks; sample all legal start dates for a full or terminal partial block.
    n,remainder = divmod(len(values),length)
    starts = rng.integers(0,len(values)-length+1,size=(draws,n))
    sums = (prefix[starts+length]-prefix[starts]).sum(axis=1)
    if remainder:
        last = rng.integers(0,len(values)-remainder+1,size=draws)
        sums += prefix[last+remainder]-prefix[last]
    interval = np.expm1(np.quantile(sums*365.25/len(values),[.025,.975]))*100
    daily = days.assign(paired_log_change=delta)
    daily.to_csv(EXP/f'paired_daily_{name}.csv.gz',index_label='utc_trading_day')
    return dict(days=len(values),start=str(days.index[0].date()),end=str(days.index[-1].date()),
        terminal_ratio=float(gross),years_outperforming=int(annual.gt(1e-12).sum()),
        years={str(y):float(np.exp(x)) for y,x in annual.items()},
        largest_contributing_quarter=str(quarters.idxmax()),
        largest_quarter_fraction_of_total_log_advantage=float(quarters.max()/delta.sum()),
        ratio_excluding_largest_quarter=float(np.exp(delta.sum()-quarters.max())),
        point_annualized_ratio_gain_pct=float(100*np.expm1(delta.sum()*365.25/len(values))),
        descriptive_90d_block_annualized_ratio_gain_pct=interval.tolist())


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed paired calendar check')
    ok = ROOT/'exp-cloud-close-vix-priority-medians-20261109-v1/aligned'
    switch = ROOT/'exp-cloud-close-doubling-sale-fraction-20261109-v1'
    pairs = {'OKX':(ok/'hourly_median.csv.gz', switch/'hourly_no_doubling_sale.csv.gz')}
    base = ROOT/'exp-cloud-close-binance-capacity-funding-20261109-v1'
    variant = ROOT/'exp-cloud-close-binance-doubled-stop-transfer-20261109-v1'
    for boundary in ['include_boundary','exclude_boundary']:
        paths = list((base/boundary).glob('*hourly*.csv.gz'))
        if len(paths)!=1:
            raise ValueError('Ambiguous Binance source hourly account')
        pairs['binance_'+boundary] = (paths[0],variant/('no_doubling_sale_'+boundary)/'hourly.csv.gz')
    out = {name:compare(name,*paths) for name,paths in pairs.items()}
    (EXP/'summary.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(out,flush=True)


if __name__=='__main__':
    main()
