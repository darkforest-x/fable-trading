"""Reconcile frozen ICT artifacts without reading prices or rerunning exits."""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments/active/exp-spike-eth3m-ict-sessions-20260914-v1'
FREEZE = '69c9c7636bed121a872b7e72fd63a534b590a00c'


def longest(values):
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def main():
    out = EXP / 'results'
    summary = pd.read_csv(out / 'summary.csv')
    cash = pd.read_csv(out / 'cash.csv')
    controls = pd.read_csv(out / 'controls.csv')
    assert (len(summary), len(cash), len(controls)) == (96, 192, 72)
    windows = dict(all=[(0, 24)], london=[(2, 5)], lunch=[(5, 8)],
                   new_york=[(8, 11)], union=[(2, 11)], london_new_york=[(2, 5), (8, 11)])
    for row in summary.itertuples():
        tag = f'{row.period}_{row.arm}_{row.session}'
        trades = pd.read_csv(out / (tag + '_serial.csv.gz'))
        hours = pd.to_datetime(trades.entry_time, utc=True).dt.tz_convert('America/New_York').dt.hour
        assert all(any(start <= hour < end for start, end in windows[row.session]) for hour in hours)
        assert trades.signal_i.is_monotonic_increasing and not trades.signal_i.duplicated().any()
        for before, after in zip(trades.to_dict('records'), trades.to_dict('records')[1:]):
            opposite = before['side'] != after['side']
            assert (after['signal_i'] > before['exit_i'] or
                    (after['signal_i'] == before['exit_i'] and opposite) or
                    (after['entry_i'] == before['exit_i'] and before['exit_at_open'] and opposite))
        natural = trades[~trades.censored]
        assert len(natural) == row.natural
        assert longest(natural.net_r < -1e-9) == row.max_net_loss_streak
        assert longest(natural.exit_reason.isin(['initial_stop', 'initial_stop_gap'])) == row.max_initial_stop_streak
        np.testing.assert_allclose(natural.net_r.sum(), row.sum_net_r, atol=1e-8)
        np.testing.assert_allclose(trades.gross_r - trades.net_r, .002 / trades.initial_risk_frac, atol=1e-8)
    for row in cash.itertuples():
        ledger = pd.read_csv(out / f'{row.period}_{row.arm}_{row.session}_{row.cash}_ledger.csv.gz')
        accepted = ledger[ledger.accepted]
        assert len(accepted) == row.n_accepted
        assert len(accepted[accepted.censored == False]) == row.n_natural
        np.testing.assert_allclose(1000 + ledger.pnl.sum(), row.final_balance, atol=1e-8)
        np.testing.assert_allclose(ledger.equity_before + ledger.pnl, ledger.equity_after, atol=1e-8)
        np.testing.assert_allclose(accepted.risk * accepted.net_r, accepted.raw_pnl, atol=1e-8)
        assert all(ledger[~ledger.accepted].pnl == 0)
        for previous, current in zip(ledger.to_dict('records'), ledger.to_dict('records')[1:]):
            np.testing.assert_allclose(previous['equity_after'], current['equity_before'], atol=1e-8)
    for row in controls.itertuples():
        matched = pd.read_csv(out / f'{row.period}_{row.arm}_{row.session}_matched.csv.gz')
        full = matched[matched.matched == 9]
        assert len(full) == row.matched_trades and len(matched) - len(full) == row.unmatched
        for trade in full.itertuples():
            draws = json.loads(trade.draws)
            assert len(draws) == len({d['signal_i'] for d in draws}) == 9
            np.testing.assert_allclose(np.mean([d['net_r'] for d in draws]), trade.random_net_r, atol=1e-8)
        np.testing.assert_allclose(full.actual_net_r - full.random_net_r, full.excess_net_r, atol=1e-8)
        np.testing.assert_allclose(full.excess_net_r.mean(), row.excess_net_r, atol=1e-8)
    cfg = json.loads((EXP / 'config.json').read_text())
    baseline = ROOT / cfg['baseline_csv']
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == cfg['baseline_sha256']
    old = pd.read_csv(baseline)
    new = pd.read_csv(out / 'development_original_all_serial.csv.gz')
    assert len(old) == len(new) == 744
    for col in ['signal_i', 'side', 'entry_time', 'exit_reason']:
        assert old[col].equals(new[col]), col
    assert old.exit_time_raw.equals(new.exit_time)
    for col in ['entry_price', 'initial_stop', 'exit_price', 'net_r', 'gross_r']:
        np.testing.assert_allclose(old[col], new[col], atol=1e-10)
    source = 'yoyo/evaluation/spike_ict_session_study.py'
    frozen = subprocess.check_output(['git', 'show', FREEZE + ':' + source], cwd=ROOT)
    assert frozen == (ROOT / source).read_bytes()
    selection_ns = (EXP / 'selection.json').stat().st_mtime_ns
    first_validation_ns = min(p.stat().st_mtime_ns for p in out.glob('validation_*'))
    assert selection_ns < first_validation_ns
    receipt = dict(status='passed', serial_groups=96, cash_ledgers=192, control_groups=72,
        original_baseline_rows_identical=744, holdout_consumptions=0,
        study_frozen_commit=FREEZE, runner_sha256=hashlib.sha256(frozen).hexdigest(),
        runner_unchanged_since_freeze=True, selection_mtime_ns=selection_ns,
        first_validation_artifact_mtime_ns=first_validation_ns,
        provenance_note='Original receipt builder_commit captured end-of-run HEAD, not run-start HEAD.',
        effective_arms=['original', 'next_bar', 'ohlc', 'olhc'],
        effective_cash={'fixed1': {'schedule': 'fixed', 'leverage_cap': 10},
                        'double': {'schedule': 'double', 'leverage_cap': 10}},
        inherited_unused_config_keys=['arms', 'cash', 'permutation_draws'],
        limitations='Artifact arithmetic and baseline parity, not actual ticks or Pine chart trade parity.')
    (out / 'validation_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
