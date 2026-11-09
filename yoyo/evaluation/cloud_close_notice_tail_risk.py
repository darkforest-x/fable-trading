"""Transfer the earlier minute risk evidence only along an identical portfolio prefix."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_notice_tail_wallet as correction
from yoyo.evaluation import cloud_close_waves_minute_risk as prior

EXP = correction.EXP.parent/'exp-cloud-close-notice-tail-risk-20261109-v1'
BASE = correction.EXP/'repaired'


def inputs():
    rows = pd.read_csv(BASE/'selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    src = correction.parent.repair.market.sources()
    src['WAVES'] = (correction.parent.repair.WAVES,None,src['WAVES'][2])
    notices = json.loads((correction.sources.EXP/'config.json').read_text())['symbols']
    for symbol,spec in notices.items():
        end = pd.Timestamp(spec['end']).value//1_000_000
        src[symbol] = (correction.sources.EXP/symbol/'bars1h.csv.gz',correction.sources.EXP/symbol/'bars12h.csv.gz',end)

    @lru_cache(maxsize=None)
    def quotes(symbol):
        path,_,end = src[symbol]
        bars = pd.read_csv(path,float_precision='round_trip')
        bars = bars[bars.time.lt(end)]
        return bars.time.to_numpy(),bars[correction.archive.FIELDS].to_numpy()

    return rows,quotes,src


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed corrected-source prefix assessment')
    tape,quotes,_ = inputs()
    original = correction.parent.old
    changed = set(json.loads((correction.EXP/'summary.json').read_text())['repriced_original'])
    targets = tape[tape.event_id.isin(changed)]
    native = original.parent.new_exits(targets,quotes)
    cached = original.changed_limits(targets,quotes)
    for field in ['exit_bar_ms','exit_price','exit_fee_rate','exit_phase']:
        left = native.set_index('event_id')[field].sort_index() if len(native) else None
        right = cached.set_index('event_id')[field].sort_index() if len(cached) else None
        if (left is None)!=(right is None) or (left is not None and not left.equals(right)):
            raise ValueError('Originally cached and corrected native doubling child disagree')
    ref = json.loads((prior.EXP/'summary.json').read_text())
    old = pd.read_csv(prior.repair.EXP/'hourly_1.5x_stress.csv.gz',index_col=0,parse_dates=True)
    new = pd.read_csv(BASE/'hourly_1.5x_stress.csv.gz',index_col=0,parse_dates=True)
    if not old.index.equals(new.index) or not old.columns.equals(new.columns):
        raise ValueError('Original and new calendar or stages differ')
    changed_hours = new.ne(old).any(axis=1)
    first = new.index[np.flatnonzero(changed_hours)[0]]
    if max(pd.to_datetime(ref['fully_refined'],utc=True)) >= first:
        raise ValueError('A previously audited hour changed')
    bounds = 100*(1-new.lower_nav/new.upper_nav.cummax().clip(lower=1000.))
    suffix_dd = float(bounds.loc[first:].max())
    if suffix_dd>=ref['refined_lower_pct']:
        raise ValueError('New suffix could set the whole-period maximum')
    result = dict(first_changed_hour_end_utc=first.isoformat(),identical_hours=int((new.index<first).sum()),
            distinct_hourly_fields=list(new.columns),suffix_full_history_peak_dd_upper_pct=suffix_dd,
            audited_prefix_hours=len(ref['fully_refined']),stress_dd_lower_pct=ref['refined_lower_pct'],
            stress_dd_upper_pct=ref['refined_upper_pct'],original_repriced_funded_parents=targets.event_id.nunique(),
            native_doublings=len(native),same_as_cached=True,exclude='Historical funding and missing marks')
    correction.archive.prices.safe_json(EXP/'summary.json',result)
    print(result)


if __name__=='__main__':
    main()
