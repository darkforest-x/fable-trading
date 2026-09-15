"""Pre-May OKX ETH ChartArt v1.1 sizing study; no parameter optimization.

Price bytes are parsed only after source timestamp approval. Matched random
entries use same symbol, side, UTC month and current BB-width bucket; their
exit is the next opposite original signal, exactly as for the target trade.
Random control outcomes are labels, never sizing/entry inputs. Account sizes
are delegated to the event-ordered accounting module. No live path imports.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.data.release_eth_prefix import validate_ohlcv
from yoyo.evaluation.chartart_bbrsi import features, replay, trade_row
from yoyo.evaluation.chartart_martingale_account import simulate_account

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-chartart-bbrsi-martingale-20260915-v1'
DEPENDENCIES = ['yoyo/evaluation/chartart_bbrsi.py', 'yoyo/evaluation/chartart_bbrsi_study.py',
                'yoyo/evaluation/chartart_martingale_account.py', 'yoyo/data/release_eth_prefix.py',
                'yoyo/contracts/holdout.py', 'tests/evaluation/test_chartart_bbrsi.py',
                'tests/evaluation/test_chartart_martingale_account.py',
                'tests/evaluation/test_chartart_bbrsi_study.py',
                str((EXP/'config.json').relative_to(ROOT)), str((EXP/'PROJECT_PLAN.md').relative_to(ROOT)),
                str((EXP/'source_receipt.json').relative_to(ROOT))]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def read_prefix(path, minutes, end):
    """Generalized timestamp-first reader; forbidden row OHLC bytes unread."""
    end = pd.Timestamp(end)
    if minutes not in (1, 3, 5, 15) or end.tzinfo is None or end > min(pd.Timestamp(HOLDOUT_START), pd.Timestamp('2026-05-01T00:00Z')):
        raise ValueError('unsupported timeframe or restricted endpoint')
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    rows, excluded = [], None
    with path.open('rb') as f:
        header = f.readline()
        names = next(csv.reader([header.decode('utf-8-sig').strip()]))
        if names[0] != 'ts' or not {'open','high','low','close','volume'} <= set(names):
            raise ValueError('unexpected source header')
        digest.update(header)
        while True:
            token = bytearray()
            while True:
                c = f.read(1)
                if c in (b'', b','): break
                if c in (b'\r', b'\n') or len(token) >= 24: raise ValueError('bad timestamp')
                token.extend(c)
            if not token and c == b'': break
            if c != b',': raise ValueError('truncated timestamp')
            stamp = pd.Timestamp(int(token), unit='ms', tz='UTC')
            if stamp + pd.Timedelta(minutes=minutes) > end:
                excluded = str(stamp)
                break
            raw = bytes(token)+b','+f.readline()
            fields = next(csv.reader([raw.decode().strip()]))
            if len(fields) != len(names): raise ValueError('bad row width')
            m = dict(zip(names, fields))
            rows.append((stamp, *[float(m[k]) for k in ['open','high','low','close','volume']]))
            digest.update(raw)
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise ValueError('source changed during read')
    f = pd.DataFrame(rows, columns=['open_time','open','high','low','close','volume'])
    if f.empty: raise ValueError('no approved rows')
    validate_ohlcv(f, minutes)
    return f.set_index('open_time'), dict(path=str(path), prefix_sha256=digest.hexdigest(), rows=len(f),
        first_open=str(f.open_time.iloc[0]), last_close=str(f.open_time.iloc[-1]+pd.Timedelta(minutes=minutes)),
        excluded_timestamp_only=excluded, restricted_price_rows_parsed=0, holdout_consumed=False,
        source_size_metadata_only=before.st_size, gaps=0, duplicates=0)


def matched_controls(frame, trades, start, end, seed, draws):
    """One draw per deterministic seed; censored draws retained without retry."""
    rng = np.random.default_rng(seed)
    month = frame.index.strftime('%Y-%m')
    bins = np.searchsorted([.01,.02,.04,.08,.16], frame.bandwidth.to_numpy(), side='left')
    eligible = (frame.index >= pd.Timestamp(start)) & (frame.index < pd.Timestamp(end)) & frame.bandwidth.shift(1).notna().to_numpy()
    # Control decision occurs at the PREVIOUS bar close, never using entry-bar features.
    bins = np.roll(bins, 1)
    eligible[0] = False
    rows = []
    opposite = {1: np.flatnonzero(frame.short_signal.to_numpy())+1,
                -1: np.flatnonzero(frame.long_signal.to_numpy())+1}
    for ti, t in trades.iterrows():
        i, side = int(t.entry_i), int(t.side)
        choices = np.flatnonzero(eligible & (month == month[i]) & (bins == bins[i]) & (np.arange(len(frame)) != i))
        for draw in range(draws):
            j = None if not len(choices) else int(rng.choice(choices))
            result = None
            if j is not None:
                future = opposite[side][(opposite[side] > j) & (opposite[side] < len(frame))]
                result = trade_row(frame, j, int(future[0]) if len(future) else len(frame)-1,
                                   side, censored=not len(future))
            matched = result is not None and not bool(result['censored']) and not bool(t.censored)
            rows.append(dict(trade_id=int(ti), draw=draw, side=side, entry_time=t.entry_time,
                control_entry_time=None if result is None else result['entry_time'],
                target_net_return=float(t.net_return), control_net_return=None if result is None else result['net_return'],
                target_censored=bool(t.censored), control_censored=None if result is None else result['censored'],
                matched=matched, vol_bin=int(bins[i]), month=month[i]))
    return pd.DataFrame(rows)


def statistics(trades, controls, seed):
    closed = trades.loc[~trades.censored]
    g, n = closed.gross_return.to_numpy(), closed.net_return.to_numpy()
    losses = 0; max_losses = 0
    for x in n:
        losses = losses+1 if x < 0 else 0
        max_losses = max(max_losses, losses)
    wins = n[n>0]; loss = n[n<0]
    info = dict(closed=len(closed), open=int(trades.censored.sum()), gross_win_rate=float(np.mean(g>0)) if len(g) else None,
        net_win_rate=float(np.mean(n>0)) if len(n) else None,
        mean_gross_bp=float(np.mean(g)*1e4) if len(g) else None, mean_net_bp=float(np.mean(n)*1e4) if len(n) else None,
        profit_factor=float(wins.sum()/-loss.sum()) if len(loss) else None,
        avg_win_bp=float(wins.mean()*1e4) if len(wins) else None,
        avg_loss_bp=float(loss.mean()*1e4) if len(loss) else None,
        max_consecutive_losses=max_losses, max_loss_bp=float(n.min()*1e4) if len(n) else None,
        worst_open_mae_bp=float(trades.mae_return.min()*1e4) if len(trades) else None)
    matches = controls.loc[controls.matched].copy() if len(controls) else pd.DataFrame()
    if len(matches):
        paired = matches.groupby('trade_id').agg(target=('target_net_return','first'), control=('control_net_return','mean'), entry_time=('entry_time','first'))
        paired['diff'] = paired.target-paired.control
        weeks = pd.to_datetime(paired.entry_time, utc=True).dt.strftime('%G-%V')
        block = paired.groupby(weeks)['diff'].agg(['sum','count'])
        rng = np.random.default_rng(seed)
        null = (rng.choice([-1,1], (4000,len(block))) * block['sum'].to_numpy()).sum(axis=1)/block['count'].sum()
        p = (1+int((null>=paired['diff'].mean()-1e-15).sum()))/4001
        info.update(control_matched_targets=len(paired), control_mean_net_bp=float(paired.control.mean()*1e4),
            matched_target_mean_net_bp=float(paired.target.mean()*1e4), excess_net_bp=float(paired['diff'].mean()*1e4),
            block_count=len(block), p_value=p)
    else:
        info.update(control_matched_targets=0, control_mean_net_bp=None, excess_net_bp=None, p_value=None, block_count=0)
    return info


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    hashes = {}
    for rel in DEPENDENCIES:
        if subprocess.check_output(['git','show','HEAD:'+rel], cwd=ROOT) != (ROOT/rel).read_bytes():
            raise ValueError('uncommitted builder '+rel)
        hashes[rel] = sha(ROOT/rel)
    out = EXP/'results'
    out.mkdir(exist_ok=False)
    save(out/'started.json', dict(config=cfg, builders=hashes,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        holdout_consumed=False, holdout_consumption_number=0))
    summaries, receipts = [], []
    for tf in cfg['timeframes']:
        frame, receipt = read_prefix(ROOT/cfg['sources'][str(tf)], tf, cfg['end'])
        frame = features(frame)
        receipts.append(dict(minutes=tf, **receipt))
        for window in cfg['windows'][str(tf)]:
            start = window['start']; label = window['name']
            if (frame.index < pd.Timestamp(start)).sum() < 250:
                raise ValueError('insufficient warmup')
            folder = out/f'{tf}m_{label}'
            folder.mkdir()
            trades = replay(frame, start, cfg['end'], tf)
            trades.to_csv(folder/'unit_trades.csv', index=False)
            controls = matched_controls(frame,trades,start,cfg['end'],cfg['seed']+tf,cfg['control_draws'])
            controls.to_csv(folder/'matched_controls.csv.gz',index=False)
            metrics = statistics(trades,controls,cfg['seed']+tf)
            accounts = []
            for multiplier in [1.,2.]:
                for leverage in cfg['entry_margin_leverages']:
                    summary, sized, curve = simulate_account(frame,trades,multiplier=multiplier,
                        initial_cash=cfg['initial_cash'],base_notional=cfg['base_notional'],
                        max_leverage=leverage,cost=cfg['cost'])
                    name=f'm{multiplier:g}_l{leverage:g}'
                    sized.to_csv(folder/f'{name}_trades.csv',index=False)
                    curve.to_csv(folder/f'{name}_curve.csv.gz',index=False)
                    accounts.append(dict(multiplier=multiplier, margin_leverage=leverage, **summary))
            record=dict(minutes=tf, window=label, start=start, end=cfg['end'],
                        evaluated_bars=int((frame.index>=pd.Timestamp(start)).sum()),
                        long_signals=int(frame.loc[frame.index>=pd.Timestamp(start),'long_signal'].sum()),
                        short_signals=int(frame.loc[frame.index>=pd.Timestamp(start),'short_signal'].sum()),
                        metrics=metrics, accounts=accounts)
            save(folder/'summary.json',record)
            summaries.append(record)
            print(json.dumps(record,ensure_ascii=False,default=str), flush=True)
    valid = [(i,r['metrics']['p_value']) for i,r in enumerate(summaries) if r['metrics']['p_value'] is not None]
    ceiling=0.
    for rank,(i,p) in enumerate(sorted(valid,key=lambda x:x[1])):
        ceiling=max(ceiling,min(1.,p*(len(valid)-rank)))
        summaries[i]['metrics']['p_holm']=ceiling
    save(out/'inputs.json',receipts)
    save(out/'summary.json',summaries)
    save(out/'manifest.json',dict(holdout_consumed=False,holdout_consumption_number=0,
        files={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}))


if __name__ == '__main__':
    run()
