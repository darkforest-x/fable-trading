"""US macro release windows and Monday1618 validity (method15).

Owner2026-10-23 chose this: Beijing20-24 (which contains the20:30 Beijing
US data prints) held less often on OKX. Hypothesis frozen in config before
reading outcomes: a first weekly1618 touch whose bar opens within +-2h of a
scheduled US release is more often a news-driven move that breaks the level.
Events from official sources saved under inputs/ (no hand-typed dates):
  CPI and Employment Situation: BLS archived release filenames, 08:30 ET
  FOMC: federalreserve.gov calendars, scheduled decision day, 14:00 ET
        (unscheduled calls, cancelled meetings and notation votes excluded;
        2011-2012 statements came at12:30/14:15 ET, inside the window).
Primaries (Holm over two): Binance alt ledger2020-01..2025-05 (method14
output) and XAUUSD spot2011-2026. Pass for a primary: news-window validity
below the other touches with within-week permutation p<0.01 (Holm) and
below38.2percent. OKX alts2025-2026 and Binance majors are descriptive.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_1618_alt_hours import within_week_p, boot_ci, rate

EXP = ROOT/'experiments/active/exp-monday-1618-macro-20261023-v1'
A = ROOT/'experiments/active'
MONTHS = {m: i+1 for i, m in enumerate(['January','February','March','April','May','June','July','August',
                                         'September','October','November','December'])}
SHORT = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}


def decision_day(year, month_text, days_text):
    """Last day of a meeting given e.g. ('Apr/May','30-1') or ('July','31-August 1')."""
    days_text = days_text.replace('*', '').strip()
    months = [SHORT[x[:3]] for x in month_text.split('/') if x[:3] in SHORT]
    m = re.match(r'(\d+)\s*-\s*([A-Za-z]+)\s+(\d+)', days_text)
    if m:
        return pd.Timestamp(year, SHORT[m.group(2)[:3]], int(m.group(3)))
    m = re.match(r'(\d+)\s*-\s*(\d+)', days_text)
    if m:
        d1, d2 = int(m.group(1)), int(m.group(2))
        return pd.Timestamp(year, months[-1] if d2 < d1 else months[0], d2)
    return pd.Timestamp(year, months[0], int(re.match(r'(\d+)', days_text).group(1)))


def fomc(inputs):
    out = []
    t = (inputs/'fomccalendars.htm').read_text()
    heads = sorted((m.start(), int(m.group(1))) for m in re.finditer(r'(\d{4}) FOMC Meetings', t))
    for k, (s, y) in enumerate(heads):
        e = heads[k+1][0] if k+1 < len(heads) else len(t)
        for month, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', t[s:e], re.S):
            if 'notation' in days or 'unscheduled' in days or 'cancel' in days:
                continue
            out.append(decision_day(y, month, days))
    for path in sorted(inputs.glob('fomchistorical*.htm')):
        y = int(re.search(r'(\d{4})', path.name).group(1))
        for h in re.findall(r'<h5[^>]*>([^<]*Meeting[^<]*)</h5>', path.read_text()):
            if 'unscheduled' in h or 'cancel' in h:
                continue
            body = h.split(' Meeting')[0].strip()
            m = re.match(r'([A-Za-z/]+)\s+(.+)$', body)
            out.append(decision_day(y, m.group(1), m.group(2)))
    return sorted(set(out))


def events(inputs):
    bls = json.loads((inputs/'bls_release_dates.json').read_text())
    rows = []
    for kind in ('cpi', 'empsit'):
        for d in bls[kind].split(','):
            day = pd.Timestamp(int(d[4:]), int(d[:2]), int(d[2:4]))
            rows.append((kind, day+pd.Timedelta(hours=8, minutes=30)))
    for day in fomc(inputs):
        rows.append(('fomc', day+pd.Timedelta(hours=14)))
    ev = pd.DataFrame(rows, columns=['kind', 'local'])
    ev['utc'] = pd.DatetimeIndex(ev.local).tz_localize('America/New_York').tz_convert('UTC')
    return ev.sort_values('utc').reset_index(drop=True)


def flag(touch_times, ev, hours):
    t = pd.DatetimeIndex(pd.to_datetime(touch_times, utc=True)).asi8
    e = pd.DatetimeIndex(ev.utc).asi8
    i = np.searchsorted(e, t)
    w = hours*3600*10**9
    near = np.zeros(len(t), bool); side = np.zeros(len(t), int); kind = np.array(['']*len(t), dtype=object)
    for j in (i-1, i):
        ok = (j >= 0) & (j < len(e))
        jj = np.clip(j, 0, len(e)-1)
        d = t-e[jj]
        hit = ok & (np.abs(d) <= w) & ~near
        near |= hit; side[hit] = np.sign(d[hit]); kind[hit] = ev.kind.to_numpy()[jj[hit]]
    return near, side, kind


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_macro.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    ev = events(EXP/'inputs')
    ev.assign(local=ev.local.astype(str), utc=ev.utc.astype(str)).to_csv(EXP/'events.csv', index=False)
    sets = dict(alt=pd.read_csv(A/'exp-monday-1618-alt-hours-20261023-v1/touches_alt.csv.gz'),
                xau=pd.read_csv(A/'exp-monday-1618-xau-spot-20261023-v1/touches_xau.csv.gz'),
                okx=pd.read_csv(A/'exp-monday-1618-validity-20261023-v1/touches.csv.gz'),
                majors=pd.read_csv(A/'exp-monday-1618-flow-20261023-v1/touches_flow.csv.gz'))
    rng = np.random.default_rng(cfg['seed']); res = {}
    for name, d in sets.items():
        d = d.reset_index(drop=True)
        near, side, kind = flag(d.touch_time, ev, cfg['window_hours'])
        p, gap = within_week_p(d, ~near, rng)  # one-sided: away-from-news validity minus news
        res[name] = dict(news=rate(d[near]), other=rate(d[~near]), gap_other_minus_news_pp=gap, within_week_p=p,
            news_week_boot95=boot_ci(d, near, rng, 1000) if near.sum() else None,
            before=rate(d[near & (side < 0)]), after=rate(d[near & (side >= 0)]),
            by_kind={k: rate(d[near & (kind == k)]) for k in ('cpi', 'empsit', 'fomc')})
    ps = np.array([res[n]['within_week_p'] for n in ('alt', 'xau')])
    order = np.argsort(ps); adj = np.empty(2); adj[order] = np.maximum.accumulate(np.minimum(1, ps[order]*np.array([2, 1])))
    passed = {}
    for n, a in zip(('alt', 'xau'), adj):
        res[n]['holm_p'] = float(a)
        passed[n] = bool(a < .01 and res[n]['news']['valid_pct'] < 100/2.618)
    summary = dict(config=cfg, events=dict(total=len(ev), **ev.kind.value_counts().to_dict(),
        first=str(ev.utc.min()), last=str(ev.utc.max())), results=res, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(events=summary['events'], results=res, passed=passed), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
