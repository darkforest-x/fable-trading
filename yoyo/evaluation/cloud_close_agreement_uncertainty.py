"""Paired uncertainty for the frozen half-target V1.7/4H agreement setup on restored data."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_pair_uncertainty as uncertainty
from yoyo.evaluation import cloud_close_same_time_control as matched


def main():
    paths = repaired.assembled(set(repaired.patch.affected_tickers()))
    agreed = paths[paths.agree4h]
    real = matched.returns(agreed[~agreed.is_control],.5)
    null = matched.returns(agreed[agreed.is_control],.5)
    pairs = real.add_suffix('_signal').join(null.add_suffix('_control'), how='inner')
    summary = json.loads((repaired.EXP/'summary.json').read_text())
    if len(pairs)!=summary['agree4h_paired'] or not pairs.entry_control.gt(pairs.entry_signal).all():
        raise ValueError('Later same-contract paired identities differ from corrected account')
    ledger = pd.read_csv(repaired.EXP/'funded_agree4h_half.csv.gz')
    if not np.allclose(real.loc[ledger.parent_id].net,ledger.net_return,rtol=1e-8,atol=1e-9):
        raise ValueError('Paired fractional cash returns differ from funded ledger')
    windows = [('all','2020-01-01','2026-09-24'),('dev','2020-01-01','2025-01-01'),
               ('2025','2025-01-01','2026-01-01'),('2026','2026-01-01','2026-09-24')]
    results = {}
    for name, first, end in windows:
        lo,hi = [pd.Timestamp(d,tz='UTC').value//1_000_000 for d in [first,end]]
        subset = pairs[pairs.entry_signal.ge(lo)&pairs.last_signal.lt(hi)&pairs.last_control.lt(hi)]
        diff = subset.net_signal - subset.net_control
        interval,weeks = uncertainty.draws_by_week(diff,subset.entry_signal)
        results[name] = dict(pairs=len(subset),active_weeks=weeks,mean_net_difference_pp=100*diff.mean(),
                             interval95_pp=interval)
    result = dict(periods=results,units='percentage points of equal-notional net trade return',
                  comparison='same contract and direction, later matched time, both pass weekly 4H confirmation',
                  inference='Post-selection, 5000 four-week circular block resamples; no multiple-search adjustment.')
    (repaired.EXP/'paired_uncertainty.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(results)


if __name__=='__main__':
    main()
