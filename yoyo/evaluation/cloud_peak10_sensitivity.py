"""Same-path sensitivity to the price and risk basis; no new signals or fills.

The reference basis is the original touched 1.618. The confirmation basis uses
the confirmed 12H close and the distance from that close to the original stop.
All paths share the indicator's same first stop or EMA-flip endpoint.
"""
import numpy as np
import pandas as pd
from yoyo.evaluation.cloud_peak10_inputs import EXP, dump


def main():
    x = pd.read_csv(EXP/'signals.csv')
    x['year'] = pd.to_datetime(x.confirmed_bjt, utc=True).dt.tz_convert('Asia/Shanghai').dt.year
    x['close_basis'] = np.select([x.confirmation_basis_lower_r.gt(10), x.confirmation_basis_upper_r.gt(10),
        x.exit_reason.isin(['stop','cloud_flip'])], ['hit','possible','miss'], 'censored')
    threshold = float(x.loc[x.year<=2024, 'close_distance_r'].median())
    rows = []
    for year in [2025, 2026]:
        for basis in ['status', 'close_basis']:
            known = x[x.year.eq(year) & x[basis].isin(['hit','miss'])]
            for high, group in known.groupby(known.close_distance_r > threshold):
                rows.append(dict(year=year, basis=basis, above_median=bool(high),
                                 hit=int(group[basis].eq('hit').sum()), total=len(group)))
    hits = x[x.status=='hit']
    delay = (pd.to_datetime(hits.first_gt10_bjt) - pd.to_datetime(hits.confirmed_bjt)).dt.total_seconds()/86400
    dump(EXP/'sensitivity.json', dict(confirmation_distance_threshold_r=threshold, cohorts=rows,
        original_hit_to_confirmation_basis=hits.close_basis.value_counts().to_dict(),
        distinct_symbol_peak_candles=len(hits[['symbol', 'peak_time_bjt']].drop_duplicates()),
        first_exceeding_bar_open_days_quantiles=delay.quantile([.25,.5,.75]).to_dict(),
        ended_reference_exit_at_least_10r=int(hits.exit_reference_r.ge(10).sum()),
        hit_exit_reasons=hits.exit_reason.value_counts().to_dict()))


if __name__=='__main__':
    main()
