"""Thin-liquidity sessions: do Asia-hour or weekend1618 touches hold more (method11)?

Owner Notion task 区间1.618测试. Hypothesis frozen before reading outcomes: a
first weekly1618 touch whose touch bar opens in00:00-08:00 UTC (Asia) or on
a UTC Saturday/Sunday is more often a liquidity sweep that returns to the
Monday edge. Reads the frozen OKX ledger of exp-monday-1618-validity only;
the touch bar's open time is known at the touch, so no future is read.
Pass: in both periods thin-session validity >38.2percent and above the other
touches, later one-sided binomial p<0.01 versus38.2, later mean R>0.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump


EXP = ROOT/'experiments/active/exp-monday-1618-session-20261023-v1'


def thin(times):
    t = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    return np.asarray((t.hour < 8) | (t.dayofweek >= 5))


def stats(frame):
    from scipy.stats import binomtest
    n = len(frame)
    if not n:
        return dict(n=0)
    k = int(frame.valid.sum())
    return dict(n=n, valid_pct=100*k/n, mean_r=float(frame.r.mean()),
                p_vs_382=float(binomtest(k, n, 1/2.618, alternative='greater').pvalue))


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_session.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    data = pd.read_csv(ROOT/cfg['touch_ledger']/'touches.csv.gz')
    data['thin'] = thin(data.touch_time)
    table = {}
    for name, seg in (('earlier', 0), ('later', 1)):
        d = data[data.segment.eq(seg)]
        table[name] = dict(thin=stats(d[d.thin]), other=stats(d[~d.thin]))
    hours = pd.DatetimeIndex(pd.to_datetime(data.touch_time, utc=True)).hour
    table['by_hour_block'] = {f'{h:02d}-{h+4:02d}': stats(data[(hours >= h) & (hours < h+4)]) for h in range(0, 24, 4)}
    e, l = table['earlier'], table['later']
    passed = bool(e['thin']['valid_pct'] > 100/2.618 and l['thin']['valid_pct'] > 100/2.618
                  and e['thin']['valid_pct'] > e['other']['valid_pct'] and l['thin']['valid_pct'] > l['other']['valid_pct']
                  and l['thin']['p_vs_382'] < .01 and l['thin']['mean_r'] > 0)
    summary = dict(config=cfg, touches=len(data), table=table, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(table=table, passed=passed), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
