"""Second test of the Monday1618 after a rejection (method8).

Owner Notion task 区间1.618测试. Seven owner BTC positions entered on a later
retest rather than the first touch. Event, frozen before reading outcomes:
after the first weekly1618 touch (ledger of exp-monday-1618-validity), a
later15m bar retraces at least0.236R back inside the level while neither
the Monday near edge nor the2.0 extension has traded; a bar strictly after
that retrace bar trades the1618 again within the same NY week. The second
touch is labelled exactly like the first (edge before2.0, touch bar can
only fail, later same-bar both = fail, 14-day horizon). Under a driftless
walk the odds stay 1/2.618=38.2percent whatever happened before, so a
reliable excess means the retest carries information.
Pass: earlier AND later period validity >38.2 with one-sided binomial p<0.01
in the later period, and later mean R>0.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v


EXP = ROOT/'experiments/active/exp-monday-1618-second-touch-20261023-v1'


def second_touches(f, rows, pull, horizon_days):
    h, l, c = f.high.to_numpy(), f.low.to_numpy(), f.close.to_numpy()
    local = f.index.tz_convert(v.TZ)
    week = np.array([str(w.date()) for w in (local.normalize()-pd.to_timedelta(local.dayofweek, unit='D')).tz_localize(None)])
    out = []
    for r in rows.itertuples():
        t, side = int(r.touch_i), int(r.side)
        end_week = t
        while end_week+1 < len(f) and week[end_week+1] == r.week:
            end_week += 1
        retrace = r.level+side*pull*r.R  # inside the level by pull*R
        state = 0; t2 = -1
        for i in range(t, end_week+1):
            # A retest bar that also runs to2.0 is a failed second touch, so
            # the retest is recorded before the stop check (label sees it).
            if state == 1 and i > retrace_i and (l[i] <= r.level if side == 1 else h[i] >= r.level):
                t2 = i; break
            stop_hit = l[i] <= r.stop if side == 1 else h[i] >= r.stop
            edge_hit = h[i] >= r.edge if side == 1 else l[i] <= r.edge
            if stop_hit or (i > t and edge_hit):
                break
            if state == 0 and i > t and (h[i] >= retrace if side == 1 else l[i] <= retrace):
                state = 1; retrace_i = i
        if t2 < 0:
            continue
        end = min(len(f), t2+horizon_days*96)
        sh = (l[t2:end] <= r.stop) if side == 1 else (h[t2:end] >= r.stop)
        eh = (h[t2+1:end] >= r.edge) if side == 1 else (l[t2+1:end] <= r.edge)
        s = int(np.argmax(sh)) if sh.any() else 10**9
        e = int(np.argmax(eh))+1 if eh.any() else 10**9
        risk = abs(r.stop-r.level)
        valid = e < s
        rr = side*(r.edge-r.level)/risk if valid else (-1.0 if s < 10**9 else side*(c[end-1]-r.level)/risk)
        out.append(dict(symbol=r.symbol, week=r.week, side=side, segment=r.segment, touch_i=t, second_i=t2,
                        hours_between=(t2-t)/4, first_valid=r.valid, valid=bool(valid), resolved=min(s, e) < 10**9, r=rr))
    return out


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
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_second_touch.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    source = ROOT/cfg['touch_ledger']
    data = pd.read_csv(source/'touches.csv.gz'); vcfg = json.loads((source/'config.json').read_text())
    files = v.universe(vcfg); rows = []
    for symbol, g in data.groupby('symbol'):
        f = v.load(files[symbol]); f = f[f.index >= pd.Timestamp(vcfg['start'])]
        rows += second_touches(f, g, cfg['pull_R'], cfg['horizon_days'])
    st = pd.DataFrame(rows); st.to_csv(EXP/'second_touches.csv.gz', index=False)
    table = {name: stats(st[st.segment.eq(seg)]) for name, seg in (('earlier', 0), ('later', 1))}
    table['all'] = stats(st)
    table['by_side'] = {str(s): stats(g) for s, g in st.groupby('side')}
    table['focus'] = {s: stats(g) for s, g in st[st.symbol.isin(cfg['focus'])].groupby('symbol')}
    later, earlier = table['later'], table['earlier']
    passed = bool(earlier.get('valid_pct', 0) > 100/2.618 and later.get('valid_pct', 0) > 100/2.618
                  and later.get('p_vs_382', 1) < .01 and later.get('mean_r', -1) > 0)
    summary = dict(config=cfg, second_touches=len(st), first_touches=len(data), table=table, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(summary['table'] | dict(passed=passed, n=len(st)), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
