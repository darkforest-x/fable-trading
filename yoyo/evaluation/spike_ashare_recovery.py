"""Recover singleton-week coverage without changing the frozen evaluator.

The V7 squeeze two-bar shift rejects a one-bar input before either strategy can be
ready. After evaluation ends, a source of at most five actual sessions can
be classified from calendar/bar counts alone: V1 needs bar index >=340;
V8 needs BB200 plus preceding widths. No indicators or outcomes are rerun.
Original failures and this separate generator's hash remain in receipts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.spike_ashare_data import save
from yoyo.evaluation.spike_ashare_engine import TRADE_COLUMNS, prepare_cycles, sessions
from yoyo.evaluation.spike_ashare_study import identity, sha

SINGLETON_ERROR = 'ValueError: Length of values (2) does not match length of index (1)'


def recover_singleton_coverage(exp):
    """Restore only provably unready singleton inputs after all writers finish."""
    exp = Path(exp)
    out, data = exp/'results', exp/'data'
    errors_path = out/'evaluation_errors.json'
    if not errors_path.exists():
        return False
    errors = json.loads(errors_path.read_text())
    targets = [code for code, error in errors.items() if error == SINGLETON_ERROR]
    if not targets:
        return False
    background = json.loads((out/'background_status.json').read_text())
    progress = json.loads((data/'collection_progress.json').read_text())
    summary = json.loads((out/'summary.json').read_text())
    if (background.get('phase') != 'final_reconciliation' and background.get('status') != 'complete'
            or progress['status'] not in ('complete', 'incomplete')
            or summary['status'] not in ('complete', 'incomplete')):
        raise ValueError('coverage recovery requires finished source and evaluation writers')
    config = json.loads((exp/'config.json').read_text())
    if config['versions'] != ['v1', 'v8'] or config['timeframes'] != ['1D', '1W']:
        raise ValueError('coverage recovery is limited to the approved four configurations')
    run_id = json.loads((out/'evaluation_identity.json').read_text())
    if run_id != identity(exp/'config.json'):
        raise ValueError('frozen evaluation identity changed')
    records = {row['code']: row for row in json.loads((data/'collection_records.json').read_text())}
    calendar = pd.read_csv(data/'calendar.csv', dtype=str)
    recovery_path = out/'warmup_recovery.json'
    recovered = json.loads(recovery_path.read_text()) if recovery_path.exists() else {}
    changed = False
    for code in targets:
        record = records.get(code, {})
        source = data/'daily'/f'{code}.csv'
        if 'error' in record or sha(source) != record.get('daily_sha256'):
            raise ValueError('singleton source differs from collection receipt')
        daily = pd.read_csv(source, dtype={'date': str, 'code': str})
        # Deliberately narrow: this repair cannot admit a trade or hide an
        # unexplained gap in an older stock's history.
        if not 1 <= len(daily) <= 5 or not daily.code.eq(code).all():
            continue
        expected = [d for d in sessions(calendar) if daily.date.min() <= d <= daily.date.max()]
        if sorted(daily.date) != expected:
            raise ValueError('singleton source has unexplained exchange-session gaps')
        cycles = {tf: prepare_cycles(daily, calendar, tf, config['end']) for tf in config['timeframes']}
        if (not any(len(frame) == 1 for frame in cycles.values())
                or not all(len(frame) < 340 for frame in cycles.values())):
            continue
        coverage = [dict(code=code, version=v, timeframe=tf, rows=len(daily),
                        cycle_bars=len(frame), ready_bars=0, signals=0, first_ready=None,
                        control_unmatched=0, status='warmup_insufficient' if len(frame) else 'no_traded_bars',
                        skips={}, control_skips={})
                    for tf, frame in cycles.items() for v in config['versions']]
        derivation = dict(method='singleton_calendar_counts_without_signal_evaluation',
                          generator_sha256=sha(__file__), original_error=errors[code],
                          first_date=daily.date.min(), last_date=daily.date.max(),
                          cycle_bars={tf: len(frame) for tf, frame in cycles.items()})
        receipt_path = out/'streams'/f'{code}.json'
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if (receipt.get('coverage') != coverage or receipt.get('derivation') != derivation
                    or receipt.get('identity') != run_id
                    or receipt.get('daily_sha256') != record['daily_sha256']
                    or any(sha(out/'streams'/name) != digest for name, digest in receipt['files'].items())):
                raise ValueError('existing stream is not this verified coverage recovery')
        else:
            trades_path = out/'streams'/f'{code}.trades.csv.gz'
            trades_path.parent.mkdir(parents=True, exist_ok=True)
            if trades_path.exists():
                raise ValueError('refusing to replace an unreceipted trade artifact')
            pd.DataFrame(columns=TRADE_COLUMNS+['kind']).to_csv(
                trades_path, index=False, compression={'method': 'gzip', 'mtime': 0})
            receipt = dict(code=code, daily_sha256=record['daily_sha256'], identity=run_id,
                           coverage=coverage, derivation=derivation,
                           files={trades_path.name: sha(trades_path)},
                           generated_at=pd.Timestamp.now(tz='UTC').isoformat())
            save(receipt_path, receipt)
        recovered[code] = dict(**derivation, receipt_sha256=sha(receipt_path))
        save(recovery_path, recovered)
        del errors[code]
        save(errors_path, errors)
        changed = True
    return changed
