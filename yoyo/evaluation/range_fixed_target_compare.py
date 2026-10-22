"""Compare existing fixed targets on one frozen five-year touch cohort.

Only target distance varies. Entry, side, original wick stop, control pairing
and zero fees remain frozen. Reuse the full 1R native ledger and all annual
native target ledgers; the existing Nautilus adapter executes older 2/3/5/10R
paths. All cohort labels use entry time/side only, never future returns.
Full-sample Pareto flags are descriptive, not a fitted or promoted strategy.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_fiveyear_fixed_target import (
    CODE as EXTENSION_CODE, combine, metrics, read, restate_static_fees,
)
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, runtime_versions
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.range_v230_exit_compare import run_symbol
from yoyo.evaluation.pepe_range_exits import inference, pairs

DEFAULT = ROOT / 'experiments/active/exp-btc-touch-fixed-targets-fiveyear-20261022-v1/config.json'
CODE = ['yoyo/evaluation/range_fixed_target_compare.py', *EXTENSION_CODE]


def reuse_one_r(cfg, spec, dest):
    source = ROOT / cfg['fixed1r_results'] / spec['symbol']
    folder = dest / 'fixed_1r' / spec['symbol']
    folder.mkdir(parents=True, exist_ok=True)
    for name in ['trades.csv', 'sensitivity.csv']:
        rows = restate_static_fees(read(source / name), {'name':'fixed_1r', 'target_r':1}, cfg['round_trip_cost'])
        rows['source_ledger'] = str((source / name).relative_to(ROOT))
        rows['execution_origin'] = 'cached_fiveyear_native'
        rows.to_csv(folder / name, index=False)
    dump(folder / 'reuse.json', dict(source=[file_stamp(source / name) for name in ['trades.csv','sensitivity.csv']],
                                    rows=len(rows), new_native_positions=0, static_fee_restatement=True))


def cohorts(signal, cfg):
    entry = pd.to_datetime(signal.entry_time, utc=True)
    start, end = pd.Timestamp(cfg['start']), pd.Timestamp(cfg['end'])
    assert entry.ge(start).all() and entry.lt(end).all()
    split = start + pd.DateOffset(years=3)
    result = [('five_years', np.ones(len(signal), bool)),
              ('earlier_three_years', entry < split), ('later_two_years', entry >= split),
              ('last_year', entry >= pd.Timestamp(cfg['annual_start'])),
              ('recent_two_months', entry >= pd.Timestamp(cfg['recent_start'])),
              ('long', signal.side == 1), ('short', signal.side == -1)]
    for i in range(5):
        lo, hi = start + pd.DateOffset(years=i), start + pd.DateOffset(years=i+1)
        result.append((f'{lo.date()} to {hi.date()}', (entry >= lo) & (entry < hi)))
    return result


def analyze(cfg, dest):
    results = []; validations = []; totals = dict(new_native_positions=0, new_native_fills=0, cached_rows=0)
    for spec in cfg['symbols']:
        coin = spec['symbol']
        base = read(ROOT / cfg['source'] / coin / 'candidates.csv').set_index(['pair_id','population']).sort_index()
        for target in cfg['targets_r']:
            name = f'fixed_{target}r'; folder = dest / name / coin
            primary, lower = [read(folder / x) for x in ['trades.csv','sensitivity.csv']]
            actual = primary.set_index(['pair_id','population']).sort_index()
            pd.testing.assert_index_equal(base.index, actual.index)
            np.testing.assert_allclose(actual[['entry_i','end_i','entry','stop','side']],base[['entry_i','end_i','entry','stop','side']],rtol=0,atol=1e-8)
            assert not primary.duplicated(['pair_id','population']).any()
            assert (primary.fee_r == 0).all() and (lower.fee_r == 0).all()
            signal = primary[primary.population == 'signal']
            assert len(signal) == spec['expected_total']
            for period, mask in cohorts(signal, cfg):
                ids = signal.loc[mask, 'pair_id']
                s, r = [primary[(primary.population == pop) & primary.pair_id.isin(ids)] for pop in ['signal','random']]
                ss, rr = [lower[(lower.population == pop) & lower.pair_id.isin(ids)] for pop in ['signal','random']]
                assert len(s) == len(r) == len(ss) == len(rr) == len(ids)
                results.append(dict(symbol=coin,target_r=target,period=period,signal=metrics(s),random=metrics(r),
                    stop_first_signal=metrics(ss),stop_first_random=metrics(rr),paired_excess=inference(pairs(s,r),cfg['seed'])))
            if target == 1:
                validations.append(dict(symbol=coin,target_r=target,full_cache_reuse=True,rows=len(primary)))
                totals['cached_rows'] += len(primary)
            else:
                receipt = json.loads((folder / name / 'receipt.json').read_text())
                validations.append(dict(symbol=coin,target_r=target,audit=receipt['audit'],oracle=receipt['oracle'],
                                        source_validation=str((folder/'validation.json').relative_to(ROOT))))
                totals['new_native_positions'] += receipt['audit']['positions']
                totals['new_native_fills'] += receipt['audit']['fills']
                totals['cached_rows'] += int(primary.execution_origin.eq('cached_annual_native').sum())
        full = [r for r in results if r['symbol']==coin and r['period']=='five_years']
        for row, adjusted in zip(full, holm([r['paired_excess']['p_one_sided'] for r in full])):
            row['holm_p_over_five_targets'] = float(adjusted)
            row['descriptive_pareto'] = not any(
                other['signal']['win_rate'] >= row['signal']['win_rate'] and
                other['signal']['net_r'] >= row['signal']['net_r'] - 1e-8 and
                other['signal']['realized_drawdown_r'] <= row['signal']['realized_drawdown_r'] + 1e-8 and
                (other['signal']['win_rate'] > row['signal']['win_rate'] or
                 other['signal']['net_r'] > row['signal']['net_r'] + 1e-8 or
                 other['signal']['realized_drawdown_r'] < row['signal']['realized_drawdown_r'] - 1e-8)
                for other in full if other is not row)
    dump(dest/'summary.json',dict(config=cfg,runtime=runtime_versions(),rows=results,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        limitations='Five existing targets compared descriptively on known history. No fresh forward validation or account compounding. Same five-minute OHLC fills and stop-first sensitivity, no extra slippage/funding/capacity. R drawdown excludes unrealized exposure. Time subgroups retain matched controls by signal pair.'))
    dump(dest/'validation.json',dict(checks=validations,totals=totals,frozen_entry_stop_control_parity=True,
                                     training_eligible=False,production_eligible=False))
    print(json.dumps([r for r in results if r['period']=='five_years'],ensure_ascii=False,indent=2),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT)
    args = parser.parse_args(); dest = args.config.parent
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,str(args.config)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and configuration before execution')
    cfg = json.loads(args.config.read_text())
    assert cfg['round_trip_cost'] == 0 and cfg['targets_r'] == [1,2,3,5,10]
    if (dest/'summary.json').exists():
        print('Completed comparison exists; reuse '+str(dest/'summary.json')); return
    for spec in cfg['symbols']:
        reuse_one_r(cfg,spec,dest)
        for target in cfg['targets_r'][1:]:
            name = f'fixed_{target}r'; local = dest/name
            arm_cfg = dict(cfg,arms=[dict(name=name,target_r=target)])
            run_symbol(arm_cfg,spec,local)
            combine(arm_cfg,spec,local)
    analyze(cfg,dest)


if __name__ == '__main__':
    main()
