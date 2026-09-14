"""Saved-outcome controls for nine independently frozen V8 admission gates.

No market history is read and no outcome is used to construct a gate. Null
deletions match venue, underlying, timeframe, direction, decision month and
fixed contemporaneous ATR/price bins. Whole-asset rejection is not identified
by this within-asset null; such strata are explicitly forced, not significant.
The held-out-era ledger is reused history, not a new blind test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_be05_report import metrics, SPLIT

SEED = 14092026
N_NULL = 2000
FAMILY = 27
VOL_BINS = [-np.inf, .005, .01, .02, .05, .1, np.inf]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_bool(series: pd.Series) -> pd.Series:
    out = series.astype(str).str.lower().map({'true': True, 'false': False})
    if out.isna().any():
        raise ValueError('Unknown boolean persisted value')
    return out.astype(bool)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize persisted identities; timestamps remain UTC, not display time."""
    frame = frame.copy()
    for key in ('entry_time', 'exit_time'):
        frame[key] = pd.to_datetime(frame[key], utc=True)
    frame['censored'] = strict_bool(frame.censored)
    frame['event_key'] = frame.stream_key + ':' + frame.signal_i.astype(int).astype(str) + ':' + frame.side.astype(int).astype(str)
    return frame


def windows(frame: pd.DataFrame):
    yield 'full', frame
    yield 'earlier', frame.loc[(frame.entry_time < SPLIT) & (frame.exit_time < SPLIT)]
    yield 'later', frame.loc[frame.entry_time >= SPLIT]


def matched_deletion(frame: pd.DataFrame, *, iterations: int = N_NULL) -> dict:
    """Compare fixed-event rejection to equally numerous matched deletions.

    Strata use signal_atr_pct known at confirmation. Outcomes net_r and exit
    are used only after selection to score the null. Bins are fixed fractions,
    never ranks fit to future monthly volatility or outcome quantiles.
    """
    frame = frame.loc[~frame.censored].copy()
    frame['month'] = frame.entry_time.dt.strftime('%Y-%m')
    frame['vol_bin'] = pd.cut(frame.signal_atr_pct, VOL_BINS, labels=False).astype('Int64').astype(str)
    keys = ['venue', 'asset', 'timeframe_min', 'side', 'month', 'vol_bin']
    rng = np.random.default_rng(SEED)
    null_removed = np.zeros(iterations)
    forced = movable = 0
    for _, g in frame.groupby(keys, dropna=False, sort=True):
        values = g.net_r.to_numpy(float)
        k = int(g.gate_rejected.sum())
        if k == 0:
            continue
        if k == len(g):
            null_removed += values.sum()
            forced += k
            continue
        movable += k
        ranks = rng.random((iterations, len(g)))
        take = np.argpartition(ranks, k - 1, axis=1)[:, :k]
        null_removed += values[take].sum(axis=1)
    actual_removed = float(frame.loc[frame.gate_rejected, 'net_r'].sum())
    p = (1 + int((null_removed <= actual_removed + 1e-12).sum())) / (iterations + 1) if movable else np.nan
    return dict(null_iterations=iterations, removed_net_r=actual_removed,
                random_removed_mean_r=float(null_removed.mean()),
                selection_extra_saved_r=float(null_removed.mean() - actual_removed),
                p_matched_deletion=p, p_bonferroni_27=min(1., p * FAMILY) if np.isfinite(p) else np.nan,
                forced_rejections=forced, movable_rejections=movable,
                null_identified=bool(movable))


def fixed_effect(frame: pd.DataFrame) -> dict:
    """Separate losing rows saved and winning rows lost; no serial claim."""
    closed = frame.loc[~frame.censored]
    removed = closed.loc[closed.gate_rejected]
    kept = frame.loc[~frame.gate_rejected]
    old = metrics(frame)
    new = metrics(kept)
    winners = closed.net_r >= 10
    delta = -closed.net_r.where(closed.gate_rejected, 0)
    week = closed.entry_time.dt.tz_localize(None).dt.to_period('W').astype(str)
    blocks = pd.DataFrame({'week': week, 'delta': delta}).groupby('week').delta.agg(['sum', 'size'])
    lo = hi = np.nan
    if len(blocks) >= 12:
        rng = np.random.default_rng(SEED)
        ix = rng.integers(0, len(blocks), size=(N_NULL, len(blocks)))
        means = blocks['sum'].to_numpy()[ix].sum(axis=1) / blocks['size'].to_numpy()[ix].sum(axis=1)
        lo, hi = np.quantile(means, [.025, .975])
    return {**{f'base_{k}': v for k, v in old.items()}, **{f'kept_{k}': v for k, v in new.items()},
            'removed_closed': len(removed), 'saved_loss_r': float(-removed.loc[removed.net_r <= 0, 'net_r'].sum()),
            'lost_winner_r': float(removed.loc[removed.net_r > 0, 'net_r'].sum()),
            'fixed_delta_r': float(delta.sum()), 'original_ge10': int(winners.sum()),
            'retained_original_ge10': int((winners & ~closed.gate_rejected).sum()),
            'weekly_mean_delta_low': lo, 'weekly_mean_delta_high': hi}


def load_results(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Read only complete receipts and their typed fixed/serial CSV outputs."""
    receipts = sorted((root / 'streams').glob('*/completion.json'))
    manifest = json.loads((root / 'manifest.json').read_text())
    if len(receipts) != 3531 or not manifest.get('complete'):
        raise ValueError('Full3531-stream completion required')
    parts = {'fixed': [], 'serial': []}
    sources = []
    for rp in receipts:
        rec = json.loads(rp.read_text())
        if rec.get('status') != 'complete':
            raise ValueError(f'Incomplete stream {rp}')
        for name, expected in rec['files'].items():
            kind = next((k for k in parts if name.startswith(f'v8.{k}_') and name.endswith('.csv.gz')), None)
            if kind is None:
                continue
            path = rp.parent / name
            if digest(path) != expected:
                raise ValueError(f'File hash changed: {path}')
            g = pd.read_csv(path)
            policy = name.removeprefix(f'v8.{kind}_').removesuffix('.csv.gz')
            g['policy'] = policy
            sources.append(dict(path=str(path), sha256=expected, rows=len(g)))
            if len(g):
                parts[kind].append(g)
    outputs = [prepare(pd.concat(parts[k], ignore_index=True)) for k in ('fixed', 'serial')]
    for table in outputs:
        if table.duplicated(['policy', 'event_key']).any():
            raise ValueError('Duplicate policy/event')
    outputs[0]['gate_rejected'] = strict_bool(outputs[0].gate_rejected)
    return *outputs, sources


def run(root: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    fixed, serial, sources = load_results(root)
    rows, infer, months, srows = [], [], [], []
    for policy, g in fixed.groupby('policy'):
        if policy == 'baseline':
            continue
        for period, part in windows(g):
            for minutes in ('all', 30, 60, 240):
                tf = part if minutes == 'all' else part.loc[part.timeframe_min == minutes]
                for side in ('all', 1, -1):
                    group = tf if side == 'all' else tf.loc[tf.side == side]
                    meta = dict(policy=policy, period=period, timeframe_min=minutes, side_group=str(side))
                    rows.append({**meta, **fixed_effect(group)})
                    # Only predeclared later-year per-timeframe aggregate tests.
                    if period == 'later' and minutes != 'all' and side == 'all':
                        infer.append({**meta, **matched_deletion(group)})
        for month, part in g.groupby(g.entry_time.dt.strftime('%Y-%m')):
            months.append(dict(policy=policy, month=month, **fixed_effect(part)))
    for policy, g in serial.groupby('policy'):
        for period, part in windows(g):
            for minutes in ('all', 30, 60, 240):
                tf = part if minutes == 'all' else part.loc[part.timeframe_min == minutes]
                for side in ('all', 1, -1):
                    group = tf if side == 'all' else tf.loc[tf.side == side]
                    srows.append(dict(policy=policy, period=period, timeframe_min=minutes, side_group=str(side), **metrics(group)))
    for name, data in [('fixed_effects', rows), ('matched_null', infer), ('monthly_fixed', months), ('serial_metrics', srows)]:
        pd.DataFrame(data).to_csv(output / f'{name}.csv', index=False)
    for name, table in [('fixed_events', fixed), ('serial_trades', serial)]:
        table.to_csv(output / f'{name}.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
    receipt = dict(source_kind='saved outcomes only', sources=sources, seed=SEED, family=FAMILY,
                   null='same asset/venue/tf/side/month/fixed signalATRpct bucket matched deletion;whole-stratum removals not identified',
                   files={p.name: digest(p) for p in output.iterdir() if p.is_file()})
    (output / 'statistics_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(pd.DataFrame(srows).query("period=='full' and timeframe_min=='all' and side_group=='all'").to_string(index=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.input, args.output)
