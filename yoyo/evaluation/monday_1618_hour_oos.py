"""Out-of-sample check of a post-hoc hour pattern (method12).

Owner Notion task 区间1.618测试. exp-monday-1618-session showed, after the
fact, that OKX first touches whose bar opened 20:00-24:00 UTC held 47.5
percent (n=1437) versus 38.2 driftless. That pattern was found by looking, so
it is frozen here and judged only on data that did not produce it: Binance
UM1m first touches of BTC/ETH/SOL/BNB/XRP/DOGE from2022-11-01 to2025-05-31
(ledger exp-monday-1618-oos-1m, 1m-resolved labels). Pass: 20-24 UTC
validity one-sided binomial p<0.01 above38.2 and mean R>0; the other hours
are reported for contrast.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_1618_session import stats


EXP = ROOT/'experiments/active/exp-monday-1618-hour-oos-20261023-v1'


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_hour_oos.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    data = pd.read_csv(ROOT/cfg['touch_ledger']/'touches_1m.csv.gz')
    data = data[data.period.eq('oos_pre')]
    hour = pd.DatetimeIndex(pd.to_datetime(data.touch_time, utc=True)).hour
    late = (hour >= 20)
    table = dict(hour_20_24=stats(data[late]), other=stats(data[~late]),
                 by_symbol={s: stats(g[pd.DatetimeIndex(pd.to_datetime(g.touch_time, utc=True)).hour >= 20])
                            for s, g in data.groupby('symbol')})
    t = table['hour_20_24']
    passed = bool(t['n'] and t['p_vs_382'] < .01 and t['mean_r'] > 0)
    summary = dict(config=cfg, touches=len(data), table=table, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(table=table, passed=passed), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
