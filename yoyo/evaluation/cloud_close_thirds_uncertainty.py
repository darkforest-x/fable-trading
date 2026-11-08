"""Post-selection pairing diagnostics for the repaired 2/3 partial on each venue."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_pair_uncertainty as uncertainty
from yoyo.evaluation import cloud_close_same_time_control as matching
from yoyo.evaluation import cloud_close_thirds as thirds


def compare(actual, control, *, same_time, expected, end):
    pairs = actual.add_suffix('_signal').join(control.add_suffix('_control'),how='inner')
    clock_ok = pairs.entry_signal.eq(pairs.entry_control) if same_time else pairs.entry_signal.lt(pairs.entry_control)
    if len(pairs)!=expected or not clock_ok.all():
        raise ValueError('Control pairing diverged from the funded source')
    periods = {}
    for label,first,last in [('all','2020-01-01',end),('dev','2020-01-01','2025-01-01'),
                             ('2025','2025-01-01','2026-01-01'),('2026','2026-01-01',end)]:
        lo,hi = [pd.Timestamp(t,tz='UTC').value//1_000_000 for t in [first,last]]
        rows = pairs[pairs.entry_signal.ge(lo) & pairs.last_signal.lt(hi) & pairs.last_control.lt(hi)]
        difference = rows.net_signal - rows.net_control
        interval, weeks = uncertainty.draws_by_week(difference,rows.entry_signal)
        periods[label] = dict(pairs=len(rows),weeks=weeks,signal_mean_pct=100*rows.net_signal.mean(),
                              control_mean_pct=100*rows.net_control.mean(),difference_pp=100*difference.mean(),
                              difference_interval95_pp=interval)
    return periods


def main():
    summary = {'units':'Percentage points of equal-notional net trade return, separate from compound funded NAV.',
               'method':'5000 circular resamples of 4 consecutive NY weeks; exploratory, no search adjustment.'}
    for name,folder,end in [('OKX',repair.EXP/'thirds','2026-11-09'),('Binance',thirds.EXP,'2026-09-24')]:
        if name == 'OKX':
            paths = thirds.okx_paths()
        else:
            paths = repaired.assembled(set(repaired.patch.affected_tickers()))
        actual = matching.returns(paths[~paths.is_control],2/3)
        ledger = pd.read_csv(folder/f'funded_{name}.csv.gz')
        if not np.allclose(actual.loc[ledger.parent_id].net,ledger.net_return,rtol=1e-8,atol=1e-9):
            raise ValueError('Equal-notional split differs from the funded ledger')
        controls = matching.returns(paths[paths.is_control],2/3)
        summary[name+'_later'] = compare(actual,controls,same_time=False,
                                           expected=(10816 if name=='OKX' else 20588),end=end)
        if name == 'OKX':
            control = pd.read_csv(matching.EXP/'simultaneous_controls.csv.gz',dtype={'symbol':str},
                                  keep_default_na=False,float_precision='round_trip')
            summary['OKX_simultaneous'] = compare(actual,matching.returns(control,2/3),same_time=True,
                                                  expected=11400,end=end)
    (repair.EXP/'thirds'/'paired_uncertainty.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print({k:v['all'] for k,v in summary.items() if isinstance(v,dict)})


if __name__=='__main__':
    main()
