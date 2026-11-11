"""Compare the pre-existing causal 12h first-wick label to archived H4 fills.

The old signal timestamp is the 12h close. H4 enters four hours later. The
original first-wick criterion uses contact rank, open, close and wick share
through the 12h close. It never requires a subsequent rise, fall or no retest.
"""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[2] / 'data/research/alt-isolated-12h-wicks-20261111/expanded12'


def summarize_wicks():
    cols = ['symbol','month','side','entry_ms','first_wick']
    labels = pd.read_csv(DATA/'strategy_v1/signals.csv.gz', usecols=cols).rename(
        columns={'month':'source_month','entry_ms':'reclaim_ms'})
    rows = []
    for name, path in [('成交价止盈','h4_strategy'),('轨道价止盈','h4_rail_target')]:
        fills = pd.read_csv(DATA/'all_replay_v1'/path/'fills.csv')
        fills = fills[fills['sample'].eq('signal') & fills.group.ne('下轨未重新收高')
                      & fills.exit_reason.ne('cutoff_mark')].copy()
        fills['reclaim_ms'] = fills.entry_ms - 4 * 3600000
        tagged = fills.merge(labels,on=['symbol','source_month','side','reclaim_ms'],how='left',validate='one_to_one')
        assert tagged.first_wick.notna().all()
        for yes, subset in tagged.groupby('first_wick'):
            rows.append(dict(target=name,first_wick=bool(yes),settled=len(subset),
                             mean_net_pct=subset.net_pct.mean(),wins=subset.net_pct.gt(0).sum(),
                             win_rate_pct=100*subset.net_pct.gt(0).mean()))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    summaries = summarize_wicks()
    summaries.to_csv(HERE/'wick_coverage.csv',index=False)
    print(summaries.to_string(index=False))
