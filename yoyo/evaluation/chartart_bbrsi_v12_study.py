"""BTC/ETH OKX swap 15m study: ChartArt v1.2 long-only versus v1.1 reversal.

One variable moves between the two arms -- what happens on the flat signal.
Entries, features, costs, fills and trade accounting are shared code, so a
difference in the tables cannot come from a difference in reconstruction.

Three controls accompany every arm, because a long-only rule in a market with a
trend is mostly a bet on that trend: matched random entries (same symbol, UTC
month and prior-bar band-width bucket, exited by the same rule) test entry
timing, buy-and-hold tests whether the rule beats simply holding, and
time-in-market says how much of the window the arm was exposed at all.

Prices are read timestamp-first and stop before 2026-05-01; holdout bytes are
never parsed. Windows overlap by construction and are not independent samples.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.evaluation.chartart_bbrsi import features, replay
from yoyo.evaluation.chartart_bbrsi_study import matched_controls, read_prefix, sha, save, statistics
from yoyo.evaluation.chartart_bbrsi_v12 import buy_and_hold, compounded, exposure, replay_long_only

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-chartart-bbrsi-v12-longonly-btceth-15m-20260917-v1'
DEPENDENCIES = ['yoyo/evaluation/chartart_bbrsi.py', 'yoyo/evaluation/chartart_bbrsi_study.py',
                'yoyo/evaluation/chartart_bbrsi_v12.py', 'yoyo/evaluation/chartart_bbrsi_v12_study.py',
                'yoyo/data/release_eth_prefix.py', 'yoyo/contracts/holdout.py',
                'tests/evaluation/test_chartart_bbrsi_v12.py',
                str((EXP / 'config.json').relative_to(ROOT)), str((EXP / 'PROJECT_PLAN.md').relative_to(ROOT))]

ARMS = {'v12_long_only': replay_long_only, 'v11_reversal': replay}


def run():
    cfg = json.loads((EXP / 'config.json').read_text())
    hashes = {}
    for rel in DEPENDENCIES:
        if subprocess.check_output(['git', 'show', 'HEAD:' + rel], cwd=ROOT) != (ROOT / rel).read_bytes():
            raise ValueError('uncommitted builder ' + rel)
        hashes[rel] = sha(ROOT / rel)
    out = EXP / 'results'
    out.mkdir(exist_ok=False)
    save(out / 'started.json', dict(config=cfg, builders=hashes,
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        holdout_consumed=False, holdout_consumption_number=0))
    tf = cfg['minutes']
    summaries, receipts = [], []
    for symbol, source in cfg['sources'].items():
        frame, receipt = read_prefix(ROOT / source, tf, cfg['end'])
        frame = features(frame)
        receipts.append(dict(symbol=symbol, minutes=tf, **receipt))
        for window in cfg['windows']:
            start, label = window['start'], window['name']
            if (frame.index < pd.Timestamp(start)).sum() < cfg['minimum_warmup_bars']:
                raise ValueError('insufficient warmup')
            folder = out / f'{symbol}_{label}'
            folder.mkdir()
            hold = buy_and_hold(frame, start, cfg['end'], tf)
            arms = []
            for name, fn in ARMS.items():
                trades = fn(frame, start, cfg['end'], tf)
                trades.to_csv(folder / f'{name}_trades.csv', index=False)
                seed = cfg['arm_seeds'][name]
                controls = matched_controls(frame, trades, start, cfg['end'], seed, cfg['control_draws'])
                controls.to_csv(folder / f'{name}_controls.csv.gz', index=False)
                arms.append(dict(arm=name, seed=seed, metrics=statistics(trades, controls, seed),
                                 compounded_return=compounded(trades),
                                 **exposure(trades, frame, start, cfg['end'], tf)))
            record = dict(symbol=symbol, minutes=tf, window=label, start=start, end=cfg['end'],
                          evaluated_bars=int((frame.index >= pd.Timestamp(start)).sum()),
                          entry_signals=int(frame.loc[frame.index >= pd.Timestamp(start), 'long_signal'].sum()),
                          flat_signals=int(frame.loc[frame.index >= pd.Timestamp(start), 'short_signal'].sum()),
                          buy_and_hold=hold, arms=arms)
            save(folder / 'summary.json', record)
            summaries.append(record)
            print(json.dumps(record, ensure_ascii=False, default=str), flush=True)
    save(out / 'summary.json', dict(config=cfg, sources=receipts, windows=summaries,
                                    holdout_consumed=False, generated_at=str(pd.Timestamp.utcnow())))
    save(out / 'manifest.json', {str(p.relative_to(EXP)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()})


if __name__ == '__main__':
    run()
