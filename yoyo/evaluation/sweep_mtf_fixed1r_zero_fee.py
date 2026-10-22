"""Restate frozen annual sweep20/MTF fixed1R outcomes with zero trading fees.

Reuse Nautilus 1.231.0 parent ledgers, not a new execution simulation. The
window_both admission is fixed before inspecting gross_r. Source net/fee fields
are preserved in exports. Features, entries, stops and targets are unchanged.
Drawdown is realized independent-R drawdown by exit time, not account drawdown.
"""
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/active/exp-range-v231-sweep20-20261021-v1'
DEST = ROOT / 'experiments/active/exp-sweep-mtf-fixed1r-zero-fee-20261022-v1'


def metrics(frame):
    frame = frame.sort_values(['exit_i', 'entry_i', 'pair_id'])
    r = frame.gross_r.to_numpy()
    curve = np.r_[0., r.cumsum()]
    streak = longest = 0
    for value in r:
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
    return dict(n=len(r), wins=int((r > 0).sum()), losses=int((r < 0).sum()),
                win_rate=float((r > 0).mean()), gross_r=float(r.sum()),
                profit_factor=float(r[r > 0].sum() / -r[r < 0].sum()),
                realized_max_drawdown_r=float((np.maximum.accumulate(curve)-curve).max()),
                longest_loss_streak=longest,
                ambiguous_n=int(frame.ambiguous_legs.gt(0).sum()),
                boundary_n=int(frame.boundary_weight.gt(0).sum()))


def main():
    code = str(Path(__file__).relative_to(ROOT))
    subprocess.run(['git', 'ls-files', '--error-unmatch', code], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    assert not subprocess.check_output(['git', 'diff', 'HEAD', '--', code], cwd=ROOT)
    cfg = json.loads((SOURCE / 'config.json').read_text())
    out = dict(source=str(SOURCE.relative_to(ROOT)), start=cfg['start'], end=cfg['end'],
               strategy='sweep20 + same paired MTF arrow + fixed1R',
               framework='NautilusTrader 1.231.0 frozen native execution',
               round_trip_cost=0., execution_replayed=False,
               source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               reproduce='.venv/bin/python -m yoyo.evaluation.sweep_mtf_fixed1r_zero_fee',
               limitation='Known-history descriptive comparison; independent R, not account return. No funding/slippage.',
               symbols={}, input_files=[])
    DEST.mkdir(parents=True, exist_ok=True)
    for symbol, expected in [('BTC', 35), ('ETH', 27)]:
        paths = [SOURCE / symbol / f for f in ['admission.csv', 'fixed_1r/trades.csv', 'fixed_1r/sensitivity.csv']]
        gate, original, low = [pd.read_csv(p, float_precision='round_trip') for p in paths]
        ids = gate.loc[gate.window_both, 'pair_id']
        assert len(ids) == expected and ids.is_unique
        frames = []
        result = {}
        for label, raw in [('native_ohlc', original), ('stop_first', low)]:
            rows = raw[raw.pair_id.isin(ids)].copy()
            assert len(rows) == 2*expected and not rows.duplicated(['population', 'pair_id']).any()
            assert np.allclose(rows.gross_r, rows.net_r+rows.fee_r, atol=1e-9)
            assert np.allclose(np.abs(rows.gross_r), 1, atol=1e-8)
            assert not rows.boundary_weight.gt(0).any()
            signal = rows[rows.population == 'signal'].set_index('pair_id')
            control = rows[rows.population == 'random'].set_index('pair_id').loc[signal.index]
            for field in ['side', 'week', 'segment', 'bucket']:
                assert (signal[field] == control[field]).all(), (symbol, field)
            times = pd.to_datetime(rows.entry_time, utc=True)
            assert times.ge(pd.Timestamp(cfg['start'])).all() and times.lt(pd.Timestamp(cfg['end'])).all()
            groups = dict(all=rows, long=rows[rows.side == 1], short=rows[rows.side == -1],
                          first_half=rows[times < pd.Timestamp(cfg['midpoint'])],
                          second_half=rows[times >= pd.Timestamp(cfg['midpoint'])],
                          recent2m=rows[times >= pd.Timestamp(cfg['recent_start'])])
            result[label] = {name: {pop: metrics(g[g.population == pop])
                             for pop in ['signal', 'random']} for name, g in groups.items()}
            rows['execution_assumption'] = label
            rows['zero_fee_net_r'] = rows.gross_r
            rows['zero_fee_r'] = 0.
            frames.append(rows)
        pd.concat(frames, ignore_index=True).to_csv(DEST / f'{symbol}_trades.csv', index=False)
        out['symbols'][symbol] = result
        out['input_files'].extend(dict(path=str(p.relative_to(ROOT)), bytes=p.stat().st_size,
                                      mtime_ns=p.stat().st_mtime_ns) for p in paths)
    (DEST / 'summary.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k:v['native_ohlc']['all'] for k,v in out['symbols'].items()}, indent=2))


if __name__ == '__main__':
    main()
