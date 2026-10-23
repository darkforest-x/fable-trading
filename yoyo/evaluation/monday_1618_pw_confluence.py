"""Monday1618 that coincides with last week's high/low (method18).

Owner2026-10-23 chart: BTC short at the1618 84375.8 with last week's high
84348.2 just inside it (0.014R), nPOC above as stop guard, target Monday high.
Hypothesis frozen in config before reading any split of outcomes: when the
previous NY week's near extreme (short: high, long: low) lies within
+-0.05R of the1618, the touch holds more often. Uses the frozen ledgers'
beyond_pw feature = side*(prev-week extreme - level)/R (read before Monday
ends; >0 means the extreme is inside the level, so the touch sweeps it).
Label unchanged: Monday near edge before the2.0 extension (15m or1m ledgers).
Pass: in BOTH Binance alts2020-01..2025-05 and OKX2025-06..2026-08, confluent
validity above the non-confluent touches with within-week permutation p<0.01
(Holm over the two) and week-cluster bootstrap95 lower bound >38.2percent.
Other windows, sweep side, BTC/ETH majors and XAU spot are descriptive.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_1618_alt_hours import within_week_p, boot_ci, rate

EXP = ROOT/'experiments/active/exp-monday-1618-pw-confluence-20261023-v1'
A = ROOT/'experiments/active'


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_pw_confluence.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    sets = dict(alt=pd.read_csv(A/'exp-monday-1618-alt-hours-20261023-v1/touches_alt.csv.gz'),
                okx=pd.read_csv(A/'exp-monday-1618-validity-20261023-v1/touches.csv.gz'),
                majors=pd.read_csv(A/'exp-monday-1618-flow-20261023-v1/touches_flow.csv.gz'),
                xau=pd.read_csv(A/'exp-monday-1618-xau-spot-20261023-v1/touches_xau.csv.gz'))
    rng = np.random.default_rng(cfg['seed']); w = cfg['window_R']; res = {}
    for name, d in sets.items():
        d = d.dropna(subset=['beyond_pw']).reset_index(drop=True)
        conf = (d.beyond_pw.abs() <= w).to_numpy()
        p, gap = within_week_p(d, conf, rng)
        res[name] = dict(touches=len(d), confluent=rate(d[conf]), other=rate(d[~conf]), gap_pp=gap, within_week_p=p,
            confluent_boot95=boot_ci(d, conf, rng, 1000) if conf.sum() else None,
            swept=rate(d[conf & (d.beyond_pw > 0).to_numpy()]), not_reached=rate(d[conf & (d.beyond_pw <= 0).to_numpy()]),
            windows={str(x): rate(d[d.beyond_pw.abs() <= x]) for x in cfg['descriptive_windows']},
            bins={str(k): rate(g) for k, g in d.groupby(pd.cut(d.beyond_pw, [-np.inf, -.5, -.2, -.1, -.05, 0, .05, .1, .2, .5, np.inf]), observed=True)})
        if name == 'okx':
            for sym in ('BTC', 'XAU', 'ETH'):
                x = d[d.symbol == sym]; c = (x.beyond_pw.abs() <= w)
                res[name][f'{sym}_confluent'] = rate(x[c]); res[name][f'{sym}_other'] = rate(x[~c])
    ps = np.array([res[n]['within_week_p'] for n in ('alt', 'okx')])
    order = np.argsort(ps); adj = np.empty(2); adj[order] = np.maximum.accumulate(np.minimum(1, ps[order]*np.array([2, 1])))
    for n, a in zip(('alt', 'okx'), adj):
        res[n]['holm_p'] = float(a)
    passed = bool(all(res[n]['holm_p'] < .01 and res[n]['confluent_boot95'][0] > 100/2.618 for n in ('alt', 'okx')))
    summary = dict(config=cfg, results=res, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    EXP.mkdir(exist_ok=True); dump(EXP/'summary.json', summary)
    print(json.dumps(dict(results={n: {k: r[k] for k in ('touches','confluent','other','gap_pp','within_week_p','confluent_boot95','swept','not_reached','windows')}
                                   for n, r in res.items()}, passed=passed), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
