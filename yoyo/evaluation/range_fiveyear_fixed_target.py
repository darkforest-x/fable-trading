"""Extend frozen touch-highlight static brackets with the existing native adapter.

Source: original five-year V2.31 admissions, unchanged in the V2.37 wf module.
The target uses only entry, side and initial stop, with no future admission
features. Reuse annual native fills; replay only older candidate/control pairs.
Fee restatement is permitted only for a static target with no fee-dependent
decisions. Original cached fees remain in source_* columns and source files.
This is orchestration/accounting, not another execution engine.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, runtime_versions
from yoyo.evaluation.range_v230_exit_compare import run_symbol, CODE as NATIVE_CODE
from yoyo.evaluation.pepe_range_exits import inference, pairs

DEFAULT = ROOT / 'experiments/active/exp-eth-touch-fixed10r-fiveyear-20261022-v1/config.json'
CODE = ['yoyo/evaluation/range_fiveyear_fixed_target.py', *NATIVE_CODE]


def read(path):
    return pd.read_csv(path, float_precision='round_trip')


def restate_static_fees(rows, arm, cost):
    """Restate static fill accounting without pretending to replay native fills."""
    if set(arm) != {'name', 'target_r'} or arm['target_r'] <= 0:
        raise ValueError('Cache fee restatement supports static targets only')
    out = rows.copy()
    for col in ['fee_r', 'net_r', 'net_notional_bp', 'boundary_net_r']:
        out['source_' + col] = out[col]
    out['fee_r'] = cost * out.entry / out.risk
    out['net_r'] = out.gross_r - out.fee_r
    out['net_notional_bp'] = (out.gross_r * out.risk / out.entry - cost) * 10000
    out['boundary_net_r'] += out.boundary_weight * (out.source_fee_r - out.fee_r)
    return out


def validate_static_boundaries(rows):
    """Preserve end-of-data marks, but reject earlier forced-exit cache rows.

    The caller separately verifies mapped end_i against the frozen candidate
    horizon. A boundary gain remains a boundary gain, not a target hit.
    """
    boundary = rows.boundary_weight.gt(0)
    if not rows.boundary_weight.isin([0., 1.]).all():
        raise AssertionError('Static whole-position cache has partial boundary weight')
    if not (rows.loc[boundary, 'exit_i'] == rows.loc[boundary, 'end_i']).all():
        raise AssertionError('Cached boundary exit precedes the frozen end')
    if not (rows.exit_i <= rows.end_i).all():
        raise AssertionError('Cached exit exceeds the frozen end')


def combine(cfg, spec, dest):
    coin = spec['symbol']; arm = cfg['arms'][0]
    source = ROOT / cfg['source'] / coin
    annual = ROOT / cfg['annual_results'] / coin / arm['name']
    folder = dest / coin
    meta = json.loads((source / 'preparation.json').read_text())
    mapping = {int(k): int(v) for k, v in meta['old_to_new'].items()}
    base = read(source / 'candidates.csv')
    base['risk'] = base.side * (base.entry - base.stop)
    indexed = base.set_index(['pair_id', 'population']).sort_index()
    candles = read(source / 'candles.csv.gz')
    times = pd.to_datetime(candles.ts + 300000, unit='ms', utc=True)
    checks = []
    for name, newname in [('trades.csv', 'trades.csv'), ('sensitivity.csv', 'stop_first.csv')]:
        cached = read(annual / name)
        assert set(cached.pair_id) == set(mapping)
        validate_static_boundaries(cached)
        cached = restate_static_fees(cached, arm, cfg['round_trip_cost'])
        cached['source_trade_id'] = cached.trade_id
        cached['source_ledger'] = str((annual / name).relative_to(ROOT))
        cached['execution_origin'] = 'cached_annual_native'
        cached['pair_id'] = cached.pair_id.map(mapping)
        for col in ['entry_i', 'end_i', 'exit_i']:
            cached[col] += meta['offset']
        cached['trade_id'] = cached.population + '-' + cached.pair_id.astype(str)
        for col in ['segment', 'week', 'bucket']:
            cached[col] = [indexed.loc[(r.pair_id, r.population), col] for r in cached.itertuples()]
        delta = restate_static_fees(read(folder / arm['name'] / newname), arm, cfg['round_trip_cost'])
        delta['source_trade_id'] = delta.trade_id
        delta['source_ledger'] = str((folder / arm['name'] / newname).relative_to(ROOT))
        delta['execution_origin'] = 'new_native_zero_fee'
        assert set(cached.pair_id).isdisjoint(delta.pair_id)
        rows = pd.concat([cached, delta], ignore_index=True).sort_values(['entry_i', 'trade_id'])
        actual = rows.set_index(['pair_id', 'population']).sort_index()
        pd.testing.assert_index_equal(indexed.index, actual.index)
        for col in ['entry_i', 'end_i', 'side', 'entry', 'stop', 'segment', 'week', 'bucket', 'risk']:
            np.testing.assert_allclose(actual[col], indexed[col], rtol=0, atol=1e-8)
        assert (pd.to_datetime(actual.entry_time, utc=True) == pd.to_datetime(indexed.entry_time, utc=True)).all()
        assert (rows.exit_i > rows.entry_i).all()
        validate_static_boundaries(rows)
        np.testing.assert_allclose(rows.gross_r - rows.fee_r, rows.net_r, rtol=0, atol=1e-8)
        np.testing.assert_allclose(rows.fee_r, cfg['round_trip_cost'] * rows.entry / rows.risk, rtol=0, atol=1e-8)
        # Exact intrabar chronology is represented by native fills; this column
        # is explicitly the containing bar close, not an invented fill timestamp.
        rows['exit_bar_close'] = times.iloc[rows.exit_i.to_numpy(int)].to_numpy()
        lo, hi = pd.Timestamp('2022-12-18T02:00Z'), pd.Timestamp('2022-12-18T11:30Z')
        revision_hits = (pd.to_datetime(rows.entry_time, utc=True) < hi) & (pd.to_datetime(rows.exit_bar_close, utc=True) >= lo)
        if coin == 'ETH':
            assert not revision_hits.any(), 'New path intersects known price revision; evaluate sensitivity first'
        rows.to_csv(folder / name, index=False)
        checks.append(dict(policy=name, reused_rows=len(cached), new_rows=len(delta), total_rows=len(rows),
                           frozen_entry_stop_control_parity=True, annual_gross_outcomes_preserved=True,
                           fee_restatement_only=True, revision_path_hits=int(revision_hits.sum())))
    dump(folder / 'validation.json', dict(checks=checks, sources=[file_stamp(source / 'candidates.csv'),
         file_stamp(source / 'candles.csv.gz'), file_stamp(annual / 'trades.csv'), file_stamp(annual / 'sensitivity.csv')]))


def metrics(rows):
    ordered = rows.sort_values(['exit_i', 'entry_i', 'trade_id']).net_r.to_numpy()
    equity = np.r_[0., np.cumsum(ordered)]
    streak = longest = 0
    for value in ordered:
        streak = streak + 1 if value < -1e-9 else 0
        longest = max(longest, streak)
    loss = -rows.loc[rows.net_r < -1e-9, 'net_r'].sum()
    return dict(n=len(rows), wins=int(rows.net_r.gt(1e-9).sum()),
        win_rate=float(rows.net_r.gt(1e-9).mean()), gross_r=float(rows.gross_r.sum()),
        fee_r=float(rows.fee_r.sum()), net_r=float(rows.net_r.sum()),
        mean_r=float(rows.net_r.mean()), mean_bp=float(rows.net_notional_bp.mean()),
        profit_factor=float(rows.loc[rows.net_r > 1e-9, 'net_r'].sum() / loss) if loss else None,
        realized_drawdown_r=float((np.maximum.accumulate(equity) - equity).max()),
        longest_losing_streak=longest, ambiguous=int(rows.ambiguous_legs.sum()),
        boundary_n=int(rows.boundary_weight.gt(0).sum()), boundary_r=float(rows.boundary_net_r.sum()))


def summarize(cfg, dest):
    results = []; validation = []
    for spec in cfg['symbols']:
        coin = spec['symbol']
        primary, lower = [read(dest / coin / x) for x in ['trades.csv', 'sensitivity.csv']]
        signal = primary[primary.population == 'signal'].copy()
        assert len(signal) == spec['expected_total']
        entry = pd.to_datetime(signal.entry_time, utc=True)
        start, end = pd.Timestamp(cfg['start']), pd.Timestamp(cfg['end'])
        assert entry.ge(start).all() and entry.lt(end).all()
        periods = [('five_years', np.ones(len(signal), bool)),
                   ('earlier_four_years', entry < pd.Timestamp(cfg['annual_start'])),
                   ('last_year', entry >= pd.Timestamp(cfg['annual_start'])),
                   ('recent_two_months', entry >= pd.Timestamp(cfg['recent_start'])),
                   ('long', signal.side == 1), ('short', signal.side == -1)]
        for i in range(5):
            lo, hi = start + pd.DateOffset(years=i), start + pd.DateOffset(years=i+1)
            periods.append((f'{lo.date()} to {hi.date()}', (entry >= lo) & (entry < hi)))
        for label, mask in periods:
            ids = signal.loc[mask, 'pair_id']
            s, r = [primary[(primary.population == pop) & primary.pair_id.isin(ids)] for pop in ['signal', 'random']]
            ss, rr = [lower[(lower.population == pop) & lower.pair_id.isin(ids)] for pop in ['signal', 'random']]
            assert len(s) == len(r) == len(ss) == len(rr) == len(ids)
            result = dict(symbol=coin, period=label, signal=metrics(s), random=metrics(r),
                          stop_first_signal=metrics(ss), stop_first_random=metrics(rr),
                          paired_excess=inference(pairs(s, r), cfg['seed']))
            results.append(result)
        # Reuse the completed 1R run as an equal-entry, zero-fee comparison.
        prior = read(ROOT / cfg['fixed1r_results'] / coin / 'trades.csv')
        prior = restate_static_fees(prior, {'name':'fixed_1r', 'target_r':1}, cfg['round_trip_cost'])
        a = primary.set_index(['pair_id','population']).sort_index()
        b = prior.set_index(['pair_id','population']).sort_index()
        pd.testing.assert_index_equal(a.index, b.index)
        np.testing.assert_allclose(a[['entry_i','entry','stop','side']], b[['entry_i','entry','stop','side']], rtol=0, atol=1e-8)
        results.append(dict(symbol=coin, period='five_years_fixed1r_comparison',
                       signal=metrics(prior[prior.population=='signal']), random=metrics(prior[prior.population=='random'])))
        receipt = json.loads((dest / coin / cfg['arms'][0]['name'] / 'receipt.json').read_text())
        validation.append(dict(symbol=coin, native_audit=receipt['audit'], native_oracle=receipt['oracle'],
                               comparison_1r_admission_parity=True))
    dump(dest / 'summary.json', dict(config=cfg, runtime=runtime_versions(), rows=results,
         source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
         limitations='Historical selected target; earlier history is a retrospective extension, not fresh forward validation. Independent equal-risk R, not funded-account returns. Five-minute OHLC O-H-L-C, stop-first sensitivity; no additional slippage, funding or capacity. Annual cohorts by signal entry, controls retain original pairs.'))
    dump(dest / 'validation.json', dict(checks=validation, training_eligible=False, production_eligible=False))
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT)
    args = parser.parse_args(); dest = args.config.parent
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE, str(args.config)], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and configuration first')
    cfg = json.loads(args.config.read_text())
    assert len(cfg['arms']) == 1 and set(cfg['arms'][0]) == {'name', 'target_r'}
    if (dest / 'summary.json').exists():
        print('Completed output exists; reuse ' + str(dest / 'summary.json')); return
    for spec in cfg['symbols']:
        run_symbol(cfg, spec, dest)
        combine(cfg, spec, dest)
    summarize(cfg, dest)


if __name__ == '__main__':
    main()
