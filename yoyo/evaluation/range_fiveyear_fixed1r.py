"""Compare fixed 1R TP / original wick SL on frozen five-year V2.31 entries.

Only exit policy changes. Reuse the original 485 BTC / 385 ETH signals and
their matched controls, and the previously executed annual 1R outcomes.
The existing native exit adapter replays only older pairs; its C++ oracle
checks native price/time and supplies stop-first same-bar sensitivity.
Targets use entry and initial stop only; no future fields enter admission.
No indicator, data download, cost, risk, default or live change is involved.
"""
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v230_exit_compare import run_symbol, CODE as NATIVE_CODE

DEST = ROOT / 'experiments/active/exp-range-v231-fixed1r-fiveyear-20261022-v1'
CODE = ['yoyo/evaluation/range_fiveyear_fixed1r.py', *NATIVE_CODE]


def read(path):
    return pd.read_csv(path, float_precision='round_trip')


def combine(cfg, spec):
    coin = spec['symbol']
    source = ROOT / cfg['source'] / coin
    annual = ROOT / cfg['annual_results'] / coin / 'fixed_1r'
    folder = DEST / coin
    meta = json.loads((source / 'preparation.json').read_text())
    mapping = {int(k): int(v) for k, v in meta['old_to_new'].items()}
    base = read(source / 'candidates.csv')
    base['risk'] = base.side * (base.entry - base.stop)
    indexed = base.set_index(['pair_id', 'population']).sort_index()
    checks = []
    for name, oldname, newname in [('trades.csv', 'trades.csv', 'trades.csv'),
                                  ('sensitivity.csv', 'sensitivity.csv', 'stop_first.csv')]:
        cached = read(annual / oldname)
        assert set(cached.pair_id) == set(mapping)
        assert not cached.boundary_weight.gt(0).any()
        cached['source_trade_id'] = cached.trade_id
        cached['source_ledger'] = str((annual / oldname).relative_to(ROOT))
        cached['pair_id'] = cached.pair_id.map(mapping)
        for col in ['entry_i', 'end_i', 'exit_i']:
            cached[col] += meta['offset']
        cached['trade_id'] = cached.population + '-' + cached.pair_id.astype(str)
        for col in ['segment', 'week', 'bucket']:
            cached[col] = [indexed.loc[(r.pair_id, r.population), col] for r in cached.itertuples()]
        delta = read(folder / 'fixed_1r' / newname)
        delta['source_trade_id'] = delta.trade_id
        delta['source_ledger'] = str((folder / 'fixed_1r' / newname).relative_to(ROOT))
        assert set(cached.pair_id).isdisjoint(delta.pair_id)
        rows = pd.concat([cached, delta], ignore_index=True).sort_values(['entry_i', 'trade_id'])
        actual = rows.set_index(['pair_id', 'population']).sort_index()
        pd.testing.assert_index_equal(indexed.index, actual.index)
        for col in ['entry_i', 'end_i', 'side', 'entry', 'stop', 'segment', 'week', 'bucket', 'risk']:
            np.testing.assert_allclose(actual[col], indexed[col], rtol=0, atol=1e-8)
        assert (pd.to_datetime(actual.entry_time, utc=True) == pd.to_datetime(indexed.entry_time, utc=True)).all()
        assert (rows.exit_i > rows.entry_i).all() and not rows.boundary_weight.gt(0).any()
        np.testing.assert_allclose(rows.fee_r, cfg['round_trip_cost'] * rows.entry / rows.risk, rtol=0, atol=1e-7)
        np.testing.assert_allclose(rows.gross_r - rows.fee_r, rows.net_r, rtol=0, atol=1e-8)
        # The original candidate indices are on the same complete 5m clock.
        times = read(source / 'candles.csv.gz').ts.to_numpy() + 300000
        rows['exit_time'] = pd.to_datetime(times[rows.exit_i.to_numpy(int)], unit='ms', utc=True).astype(str)
        lo, hi = pd.Timestamp('2022-12-18T02:00Z'), pd.Timestamp('2022-12-18T11:30Z')
        revision_hits = (pd.to_datetime(rows.entry_time, utc=True) < hi) & (pd.to_datetime(rows.exit_time, utc=True) >= lo)
        if coin == 'ETH':
            assert not revision_hits.any(), 'A new 1R path needs official-price revision sensitivity'
        rows.to_csv(folder / name, index=False)
        checks.append(dict(policy=name, reused_rows=len(cached), new_rows=len(delta), total_rows=len(rows),
                           admission_and_cost_parity=True, annual_outcomes_preserved=True,
                           revision_path_hits=int(revision_hits.sum()) if coin == 'ETH' else None))
    dump(folder / 'validation.json', dict(checks=checks, sources=[file_stamp(source / 'candidates.csv'),
         file_stamp(source / 'candles.csv.gz'), file_stamp(annual / 'trades.csv'), file_stamp(annual / 'sensitivity.csv')]))


def metrics(rows):
    n = len(rows)
    return dict(n=n, target_wins=int(rows.gross_r.gt(1e-9).sum()),
        target_win_rate=float(rows.gross_r.gt(1e-9).mean()), net_wins=int(rows.net_r.gt(1e-9).sum()),
        net_win_rate=float(rows.net_r.gt(1e-9).mean()), gross_r=float(rows.gross_r.sum()),
        fee_r=float(rows.fee_r.sum()), net_r=float(rows.net_r.sum()), mean_net_r=float(rows.net_r.mean()),
        mean_net_bp=float(rows.net_notional_bp.mean()), ambiguous=int(rows.ambiguous_legs.sum()),
        target_wins_not_net_wins=int((rows.gross_r.gt(1e-9) & rows.net_r.le(1e-9)).sum()),
        boundary_n=int(rows.boundary_weight.gt(0).sum()))


def summarize(cfg):
    results = []
    for spec in cfg['symbols']:
        coin = spec['symbol']
        primary, lower = [read(DEST / coin / x).set_index('trade_id', drop=False)
                          for x in ['trades.csv', 'sensitivity.csv']]
        assert primary.population.eq('signal').sum() == spec['expected_total']
        for period, start in [('five_years', cfg['start']), ('last_year', cfg['annual_start']),
                              ('recent_two_months', cfg['recent_start'])]:
            ids = primary.loc[(primary.population == 'signal') &
                              (pd.to_datetime(primary.entry_time, utc=True) >= pd.Timestamp(start)), 'pair_id']
            data = primary[primary.pair_id.isin(ids)]
            s, r = [data[data.population == pop] for pop in ['signal', 'random']]
            ss, rr = lower.loc[s.trade_id], lower.loc[r.trade_id]
            assert len(s) == len(r) == len(ids)
            results.append(dict(symbol=coin, period=period, signal=metrics(s), random=metrics(r),
                stop_first_signal=metrics(ss), stop_first_random=metrics(rr),
                signal_changed_under_stop_first=int((np.abs(s.net_r-ss.net_r)>1e-8).sum())))
    dump(DEST / 'summary.json', dict(start=cfg['start'], end=cfg['end'], round_trip_cost=cfg['round_trip_cost'],
        rows=results, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        reproduction='PYTHONPATH=. /Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.range_fiveyear_fixed1r',
        interpretation='Gross 1:1, not cost-adjusted 1:1. Target hits may remain net losses when original risk is smaller than20bp. IndependentR, not account returns.5m OHLC execution assumptions; no funding or extra slippage.'))
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)


def main():
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE, str(DEST / 'config.json')], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and configuration first')
    cfg = json.loads((DEST / 'config.json').read_text())
    if (DEST / 'summary.json').exists():
        print('Completed output exists; reuse ' + str(DEST / 'summary.json'))
        return
    for spec in cfg['symbols']:
        run_symbol(cfg, spec, DEST)
        combine(cfg, spec)
    summarize(cfg)


if __name__ == '__main__':
    main()
