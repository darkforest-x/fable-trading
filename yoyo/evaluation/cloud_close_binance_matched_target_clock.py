"""Align the 2R expiry on original Binance 12H cloud entries before comparing actual runner allocations."""
import gc

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_doubled_stop_transfer as transfer

funding = transfer.funding
stops = transfer.stops
EXP = funding.EXP.parent/'exp-cloud-close-binance-matched-target-clock-20261109-v1'


def twelve_hour_expiry(data, quotes):
    selected = data['selected'].copy().reset_index(drop=True)
    selected['child'] = selected.slot % 4
    indexed = selected.set_index(['event_id','child'],drop=False,verify_integrity=True)
    expiry = selected.entry_ms+stops.source.STEP
    eligible = selected.child.isin([0,1]) & selected.exit_bar_ms.ge(expiry)
    old = selected.loc[eligible]
    runner = indexed.loc[pd.MultiIndex.from_arrays([old.event_id,old.child+2])].set_index(old.index)
    if ((runner.exit_bar_ms<expiry.loc[eligible]).any() or
            not np.array_equal(old[['event_id','entry_ms','entry_price','side','stop_price']].to_numpy(),
                               runner[['event_id','entry_ms','entry_price','side','stop_price']].to_numpy())):
        raise ValueError('A scheduled expiry refers to an already terminated or mismatched original runner')
    cols = ['exit_bar_ms','exit_phase','exit_reason','exit_price','exit_fee_rate']
    cols += [name for name in ['same_bar_ambiguous','gap_flag'] if name in selected]
    changed = old[cols[:5]].ne(runner[cols[:5]]).any(axis=1)
    selected.loc[eligible,cols] = runner[cols].to_numpy()
    # The unmodified odd 2R child can only hit its profit target during the first 12 hours.
    after = selected[selected.child.eq(1)&selected.exit_reason.str.startswith('take_profit')]
    if not after.exit_bar_ms.lt(after.entry_ms+stops.source.STEP).all():
        raise ValueError('Native 2R order survived its scheduled expiry')
    end = data['start']+len(data['actions'])//3*stops.wallet.HOUR
    out = stops.wallet.prepare(None,data['actions'].shape[1],data['start'],end,assigned=(selected,data['skips']),
                               quote_loader=quotes)
    if out['gap_marks']:
        raise ValueError('Expiry now needs an unobserved traded hour')
    early = selected.loc[selected.child.eq(1)&selected.exit_reason.str.startswith('take_profit')]
    orig = old.loc[old.child.eq(1)&old.exit_reason.str.startswith('take_profit')]
    return out,dict(original_late_2r=len(orig),remaining_early_2r=len(early),
                    changed_native_parents=int(old.loc[changed,'event_id'].nunique()),changed_child_windows=int(changed.sum()))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native Binance 2R time-in-force match')
    cfg = funding._json(funding.CONFIG_PATH)
    funding._validate_config(cfg)
    p = funding._prepare_real_account(cfg)
    base,info = twelve_hour_expiry(p['data'],p['quotes'])
    no_sale,_,_ = stops.doubling.route(base,p['quotes'],[1,1,3,3])
    results = {}
    for name,data,f in [('standard',base,2/3),('hold_doubling',no_sale,2/3),('small_2r',base,1/3)]:
        empty, coverage = funding.relative._check_covered_windows(data['selected'],p['tape'],p['months'])
        for k in ['expected_positions','expected_child_legs','exposure_map']:
            data[k] = p['data'][k]
        data.update(funding_no_settlement_windows=empty,funding_source_audit=coverage)
        weights = np.array([f/2,f/2,(1-f)/2,(1-f)/2])
        for slip in [0.,.0005]:
            for boundary in ['include_boundary','exclude_boundary']:
                key = name+('_stress' if slip else '')+'_'+boundary
                results[key] = transfer.scenario(p,data,empty,key,boundary,slip,directory=EXP,child_weights=weights)
                gc.collect()
    funding._write_json(EXP/'summary.json',dict(arms=results,expiry=info,native_positions=len(p['reference']),
                                              expiry_hours=12))


if __name__ == '__main__':
    main()
