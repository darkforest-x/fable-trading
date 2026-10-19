"""Independent scalar reconciliation of task62 inputs and all outcome paths.

Uses signed favorable-price coordinates, independently reconstructs Stoch and
admission, and never calls the replay/trace/serial/metrics implementation.
Only raw ingestion is shared; no model, production or account access.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_stoch_confirm_exits import EXP, load_minutes


def signed_path(a, arrows, crosses, r, endpoint):
    """Audit minute OHLC with stop/target opening priority and close amendments."""
    side = int(r.side)
    entry = side * r.entry
    floor = side * r.stop
    risk = entry - floor
    fee = .002 * r.entry
    name = r.policy
    target_r = float(name.split('_')[1][:-1]) if name.startswith('target_') else 0
    trail_r = float(name.split('_')[1][:-1]) if name.startswith('trail_') else 0
    be_r = float(name.split('_')[2][:-1]) if name.startswith('be_') else 0
    ttl = 5 * int(name.split('_')[1][:-4]) if name.startswith('time_') else 0
    destination = np.ceil((entry + (fee if '_net_' in name else 0)) * 100 - 1e-8) / 100
    target = entry + target_r * risk
    peak = entry
    protected = trailing = False
    ambiguous = 0
    reason = 'boundary'
    for j in range(r.entry_i, endpoint):
        op, hi, lo, close = a[j]
        op, best, worst, close = side * op, max(side * hi, side * lo), min(side * hi, side * lo), side * close
        price = close
        if op <= floor:
            reason, price = 'stop_gap', op
        elif target_r and op >= target:
            reason, price = 'target_gap', op
        elif j != r.entry_i and j % 5 == 0 and (crosses if name == 'opposite_kd' else arrows)[j // 5 - 1] == -side:
            reason, price = 'opposite', op
        elif ttl and j - r.entry_i >= ttl:
            reason, price = 'time', op
        elif worst <= floor:
            reason, price = 'stop', floor
            ambiguous = int(target_r > 0 and best >= target)
        elif target_r and best >= target:
            reason, price = 'target', target
        if reason != 'boundary':
            break
        peak = max(peak, close)
        if be_r and close - entry >= max(be_r * risk, destination - entry):
            floor = max(floor, destination)
            protected = True
        if trail_r and peak - entry >= 2 * risk:
            floor = max(floor, np.floor((peak - trail_r * risk) * 100 + 1e-8) / 100)
            trailing = True
    return j, side * price, reason, protected, trailing, ambiguous


def main():
    cfg = json.loads((EXP / 'config.json').read_text())
    out = EXP / 'results_v1'
    minute, _ = load_minutes(cfg)
    arr = minute[['open', 'high', 'low', 'close']].to_numpy()
    blocks = arr.reshape(-1, 5, 4)
    o, h, l, c = blocks[:, 0, 0], blocks[:, :, 1].max(axis=1), blocks[:, :, 2].min(axis=1), blocks[:, -1, 3]
    high5 = pd.Series(h).rolling(5, min_periods=1).max()
    low5 = pd.Series(l).rolling(5, min_periods=1).min()
    raw = (pd.Series(c) - low5) / (high5 - low5) * 100
    assert raw.notna().all(), 'Independent SMA below requires no missing raw input'
    k = raw.rolling(3).mean()
    d = k.rolling(3).mean()
    crosses = np.where((k > d) & (k.shift() <= d.shift()), 1, np.where((k < d) & (k.shift() >= d.shift()), -1, 0))
    arrows = np.where((crosses == 1) & (k < 20) & (d < 20), 1, np.where((crosses == -1) & (k > 80) & (d > 80), -1, 0))
    idx5 = minute.index[::5]
    candidates = pd.read_csv(out / 'signal_candidates.csv')
    eligible = []
    for i in np.flatnonzero(arrows):
        if i < 20 or i + 2 >= len(c) or idx5[i + 2] < pd.Timestamp(cfg['start']):
            continue
        direction = int(arrows[i])
        confirm = direction * (c[i + 1] - o[i + 1]) > 0
        stop = l[i - 2:i + 2].min() - 1 if direction == 1 else h[i - 2:i + 2].max() + 1
        risk = direction * (o[i + 2] - stop)
        if confirm and 0 < risk <= 8 + 1e-8:
            eligible.append((i, o[i + 2], stop, risk))
    entries = pd.read_csv(out / 'entries.csv')
    controls = pd.read_csv(out / 'control_entries.csv')
    np.testing.assert_allclose(entries[['anchor_i', 'entry', 'stop', 'risk']], np.array(eligible), atol=1e-8, rtol=0)
    assert candidates.anchor_i.tolist() == [int(i) for i in np.flatnonzero(arrows) if i >= 20 and i + 2 < len(c) and idx5[i + 2] >= pd.Timestamp(cfg['start'])]
    for r in controls.itertuples(index=False):
        s = entries[entries.pair_id.eq(r.pair_id)].iloc[0]
        assert all(getattr(r, key) == s[key] for key in ['side', 'week', 'bucket', 'segment'])
        i = r.anchor_i
        assert arrows[i] == 0 and r.side * (c[i + 1] - o[i + 1]) > 0
        stop = l[i - 2:i + 2].min() - 1 if r.side == 1 else h[i - 2:i + 2].max() + 1
        assert abs(stop - r.stop) < 1e-8 and abs(o[i + 2] - r.entry) < 1e-8
        assert 0 < r.side * (r.entry - stop) <= 8 + 1e-8
    ledgers = {}
    checks = 0
    tiny_zero = {}
    for filename in ('paths.csv.gz', 'controls.csv.gz'):
        paths = pd.read_csv(out / filename)
        for r in paths.itertuples(index=False):
            endpoint = minute.index.searchsorted(pd.Timestamp(cfg[{'development': 'split', 'validation': 'examples_start', 'examples': 'end'}[r.segment]]))
            j, price, reason, protected, trailing, ambiguous = signed_path(arr, arrows, crosses, r, endpoint)
            assert (j, reason, protected, trailing, ambiguous) == (r.exit_i, r.reason, r.be_activated, r.trail_activated, r.ambiguous_old_stop_target), (filename, r.pair_id, r.policy)
            assert abs(price - r.exit_price) < 1e-8, (filename, r.pair_id, r.policy, price, r.exit_price)
            assert r.closed == (reason != 'boundary')
            gross = r.side * (price - r.entry)
            np.testing.assert_allclose([r.gross_points, r.net_points, r.net_r, r.cost_points], [gross, gross - .002 * r.entry, (gross - .002 * r.entry) / r.risk, .002 * r.entry], atol=1e-8, rtol=0)
            assert r.entry_i <= r.exit_i < endpoint
            checks += 1
        tiny_zero[filename] = int(((paths.net_points != 0) & (paths.net_points.abs() < 1e-8)).sum())
        ledgers[filename] = paths
        print(filename, len(paths), 'verified', flush=True)
    saved = pd.read_csv(out / 'serial_trades.csv.gz')
    summary = pd.read_csv(out / 'summary.csv')
    for name, paths in ledgers.items():
        for p, rows in paths.groupby('policy'):
            selected = []
            release = -1
            for r in rows.sort_values(['entry_i', 'pair_id']).itertuples():
                if r.entry_i < release:
                    continue
                selected.append(r.Index)
                release = r.exit_i + int(not r.exit_at_open)
            chosen = paths.loc[selected]
            if name == 'paths.csv.gz':
                assert chosen.pair_id.tolist() == saved[saved.policy.eq(p)].pair_id.tolist()
            for seg in ('all', 'development', 'validation', 'examples'):
                z = chosen if seg == 'all' else chosen[chosen.segment.eq(seg)]
                closed = z[z.closed]
                summ = summary[summary.policy.eq(p) & summary.segment.eq(seg)].iloc[0]
                key = 'net_points' if name == 'paths.csv.gz' else 'random_serial_net_points'
                assert abs(closed.net_points.sum() - summ[key]) < 1e-7
                if name == 'paths.csv.gz':
                    assert len(closed) == summ.closed
                    assert abs(closed.gross_points.sum() - summ.gross_points) < 1e-7
                    assert abs(closed.cost_points.sum() - summ.cost_points) < 1e-7
                    assert abs((closed.net_points > 1e-8).mean() - summ.win_rate) < 1e-10
                    profits = closed.loc[closed.net_points > 1e-8, 'net_points'].sum()
                    losses = -closed.loc[closed.net_points < -1e-8, 'net_points'].sum()
                    assert abs(profits / losses - summ.pf) < 1e-10
                    curve = np.r_[0., closed.net_points.cumsum()]
                    assert abs(np.max(np.maximum.accumulate(curve) - curve) - summ.closed_points_drawdown) < 1e-7
    receipt = dict(status='passed', verifier_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        minute_rows=len(minute), raw_arrows=len(candidates), admission_rows=len(entries), matched_controls=len(controls),
        scalar_paths=checks, serial_policy_populations=44, summary_cells=176,
        tiny_nonzero_below_1e8=tiny_zero, zero_tolerance_points=1e-8, independence='OHLC reshape, separate Stoch, signed-price scalar paths, monetary identities and fresh serial occupancy',
        not_checked='No tick ordering or actual market execution/funding; fixed cost convention, not exchange-account-specific costs')
    (out / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
