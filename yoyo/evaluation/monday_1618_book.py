"""Order-book depth at the Monday1618 touch (method13).

Owner Notion task 区间1.618测试. Twelve price, structure, flow, session and
gold-history methods sit at driftless odds. Resting liquidity is a different
information source: Binance USD-M bookDepth archives (cumulative notional
within +-1..5percent of mid, ~30s snapshots, from2023). Touches and labels
come unchanged from the frozen flow ledger (exp-monday-1618-flow, 12 majors).
Only touch days are downloaded (plus the previous day for touches in the
first10 minutes UTC). Snapshot used: last with timestamp <= touch bar open
minus30s, so nothing after the touch is read.
book_1pct, book_2pct  side-adjusted log(notional opposing the move /
                      notional behind it) within1/2percent: short -> asks
                      over bids, long -> bids over asks.
Primary hypothesis (frozen): larger book_1pct (more resting liquidity in
front of the move) -> higher validity; later AUC one-sided permutation
p<0.01. Model: logistic on both, trained before split; success as stage1.
"""
from __future__ import annotations
import argparse
import io
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v
from yoyo.data.binance_um_vision_fetch import BASE, fetch

EXP = ROOT/'experiments/active/exp-monday-1618-book-20261023-v1'
FEATS = ['book_1pct', 'book_2pct']


def needed(rows):
    t = pd.to_datetime(rows.touch_time, utc=True)
    days = set()
    for s, x in zip(rows.symbol, t):
        days.add((s, x.date()))
        if x - x.normalize() < pd.Timedelta(minutes=10):
            days.add((s, (x - pd.Timedelta(days=1)).date()))
    return sorted(days)


def download(rows, out, workers):
    jobs = [('bookDepth', f'{s}USDT', f'{BASE}/daily/bookDepth/{s}USDT/{s}USDT-bookDepth-{d:%Y-%m-%d}.zip')
            for s, d in needed(rows)]
    with ThreadPoolExecutor(workers) as pool:
        status = list(pool.map(lambda j: fetch(out, *j), jobs))
    counts = pd.Series([x[2] for x in status]).value_counts().to_dict()
    return dict(jobs=len(jobs), counts={k: int(v_) for k, v_ in counts.items()})


def snapshot(out, symbol, t):
    """Depth rows of the last snapshot at or before t-30s, or None."""
    cut = t - pd.Timedelta(seconds=30)
    frames = []
    for d in sorted({cut.date(), (cut - pd.Timedelta(days=1)).date()}):
        path = out/'bookDepth'/f'{symbol}USDT'/f'{symbol}USDT-bookDepth-{d:%Y-%m-%d}.zip'
        if path.exists():
            with zipfile.ZipFile(path) as z:
                frames.append(pd.read_csv(io.StringIO(z.read(z.namelist()[0]).decode())))
    if not frames:
        return None
    b = pd.concat(frames)
    b['ts'] = pd.to_datetime(b.timestamp, utc=True)
    b = b[b.ts <= cut]
    if b.empty or cut - b.ts.max() > pd.Timedelta(minutes=10):
        return None
    return b[b.ts == b.ts.max()].set_index('percentage').notional.astype(float)


def features(rows, out):
    vals = []
    for r in rows.itertuples():
        snap = snapshot(out, r.symbol, pd.Timestamp(r.touch_time))
        row = dict(book_1pct=np.nan, book_2pct=np.nan)
        if snap is not None:
            for p in (1, 2):
                if p in snap.index and -p in snap.index and snap[p] > 0 and snap[-p] > 0:
                    front, behind = (snap[p], snap[-p]) if r.side == -1 else (snap[-p], snap[p])
                    row[f'book_{p}pct'] = float(np.log(front/behind))
        vals.append(row)
    return pd.DataFrame(vals, index=rows.index)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_book.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    from sklearn.metrics import roc_auc_score
    data = pd.read_csv(ROOT/cfg['touch_ledger']/'touches_flow.csv.gz')
    data = data[pd.to_datetime(data.touch_time, utc=True) >= pd.Timestamp(cfg['start'])].copy()
    data['symbol'] = data.symbol.str.replace('USDT', '', regex=False)
    out = EXP/'inputs'
    receipt = download(data, out, cfg['workers'])
    dump(out/'fetch_receipt.json', receipt)
    data = data.join(features(data, out))
    data['segment'] = (pd.to_datetime(data.touch_time, utc=True) >= pd.Timestamp(cfg['split'])).astype(int)
    data[['symbol','week','side','touch_time','valid','r','segment']+FEATS].to_csv(EXP/'touches_book.csv.gz', index=False)
    early, late = data[data.segment.eq(0)], data[data.segment.eq(1)].reset_index(drop=True)
    uni = {}
    for name in FEATS:
        uni[name] = {}
        for label, frame in (('early', early), ('late', late)):
            x = frame[[name, 'valid']].dropna()
            uni[name][f'{label}_auc'] = float(roc_auc_score(x.valid, x[name]))
            uni[name][f'{label}_quintiles'] = v.quintiles(frame, name)
    x = late[['book_1pct', 'valid']].dropna()
    rng = np.random.default_rng(cfg['seed']); obs = roc_auc_score(x.valid, x.book_1pct)
    perm = [roc_auc_score(rng.permutation(x.valid.to_numpy()), x.book_1pct) for _ in range(2000)]
    primary = dict(late_auc=float(obs), p_one_sided=float((np.sum(np.array(perm) >= obs)+1)/2001), n=len(x))
    e2, l2 = early.dropna(subset=FEATS), late.dropna(subset=FEATS).reset_index(drop=True)
    model = v.fit(e2, FEATS)
    se, sl = model.predict_proba(e2[FEATS])[:, 1], model.predict_proba(l2[FEATS])[:, 1]
    cut = float(np.quantile(se, 1-cfg['top_fraction']))
    p, _ = v.stratified_p(l2, sl, cfg['top_fraction'], cfg['seed'])
    sel = v.rate(l2[sl >= cut]); later_auc = float(roc_auc_score(l2.valid, sl))
    passed = bool(later_auc >= .55 and sel['valid_pct'] >= 100/2.618 and sel['mean_r'] > 0 and p < .01)
    summary = dict(config=cfg, receipt=receipt, touches=len(data), with_book=int(data[FEATS].notna().all(axis=1).sum()),
        base=dict(early=v.rate(early), late=v.rate(late)), univariate=uni, primary=primary,
        model=dict(coefficients=dict(zip(FEATS, map(float, model[-1].coef_[0]))), later_auc=later_auc,
                   late_selected=sel, late_top_week_stratified_p=p), passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(receipt=receipt, touches=len(data), with_book=summary['with_book'], base=summary['base'],
        primary=primary, model=summary['model'], passed=passed,
        aucs={n: (round(u['early_auc'], 3), round(u['late_auc'], 3)) for n, u in uni.items()}), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
