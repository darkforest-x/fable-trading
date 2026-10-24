"""Summarize a frozen list of completed native ledgers without replaying prices.

The owner requested an interim readout while both native pools keep running.
Existing study readers and strict matching rules are reused one symbol at a
time. Additive moments keep memory bounded; no bootstrap, chart, or execution
is launched. Boundary settlements stay censored. Both notional and original
risk payoff ratios are named explicitly rather than silently interchanged.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_limit_reclaim_analysis as limit
from yoyo.evaluation import monday_ma50_analysis as ma50
from yoyo.evaluation.monday_ma6_analysis import group_name, pairs
from yoyo.evaluation.monday_ma6_retest import finite


def moments(values):
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite economic outcome in completed ledger')
    wins, losses = values[values > 0], values[values < 0]
    return dict(n=len(values), wins=len(wins), losses=len(losses),
                total=float(values.sum()), win_sum=float(wins.sum()),
                loss_sum=float(losses.sum()))


def describe(m):
    n, wins, losses = m['n'], m['wins'], m['losses']
    mean_win = m['win_sum'] / wins if wins else None
    mean_loss = m['loss_sum'] / losses if losses else None
    return dict(n=int(n), wins=int(wins), losses=int(losses),
                flat=int(n-wins-losses), win_rate=wins/n if n else None,
                mean=m['total']/n if n else None,
                mean_win=mean_win, mean_loss=mean_loss,
                payoff=mean_win/-mean_loss if wins and losses else None,
                profit_factor=m['win_sum']/-m['loss_sum'] if losses else None)


def run(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    totals = defaultdict(lambda: defaultdict(float))
    assets = defaultdict(set)
    coverage, checks = [], []

    def add(key, prefix, data):
        for field, value in data.items():
            totals[key][prefix+field] += value

    for study, records in manifest['studies'].items():
        for number, record in enumerate(records, 1):
            done_path = Path(record['done'])
            if done_path.stat().st_mtime_ns != record['mtime_ns']:
                raise RuntimeError('Frozen completed marker changed')
            done = json.loads(done_path.read_text())
            folder, symbol = done_path.parent, record['symbol']
            if study == 'limit-reclaim':
                raw = limit.read_csv(folder/'trades.csv.gz', limit.TRADE_COLUMNS)
                t = limit.prepare_trades(raw)
                match = limit.build_strict_matches(t)
                if len(t):
                    t['snapshot_bp'] = t.return_bp
                    t['snapshot_gross_bp'] = t.gross_bp
                    t['snapshot_fee_bp'] = t.fee_bp
                    t['snapshot_period'] = t.entry_period
                minutes = done['confirm_minutes']
            else:
                raw = ma50.read(folder/'trades.csv.gz')
                t = ma50.enrich(raw) if len(raw) else raw
                match = pd.DataFrame()
                if len(t):
                    t['reclaim_minutes'] = 5
                    t['exit_mode'] = 'opposite_monday_edge'
                    t['snapshot_bp'] = t.return_pct*100
                    t['snapshot_gross_bp'] = t.gross_r*t.risk/t.entry*10000
                    t['snapshot_fee_bp'] = t.fee_r*t.risk/t.entry*10000
                    t['snapshot_period'] = t.period.map({'earlier':'pre2023','later':'post2023'})
                    np.testing.assert_allclose(t.return_pct, t.return_r*t.risk/t.entry*100)
                minutes = [5]
            assert len(raw) == done['positions'], (study, symbol)
            if len(t):
                assert t.trade_id.is_unique
                if symbol != 'XAUUSD':
                    np.testing.assert_allclose(t.snapshot_bp, t.snapshot_gross_bp-t.snapshot_fee_bp,
                                               atol=1e-7, rtol=1e-9)
                else:
                    np.testing.assert_allclose(t.snapshot_bp, t.snapshot_gross_bp, atol=1e-7)
            coverage.append(dict(study=study, symbol=symbol, first=done['first'],
                                 last=done['last'], confirm_minutes=minutes,
                                 native_positions=done['positions']))
            groups = [group_name(symbol)] + ([] if symbol == 'XAUUSD' else ['CRYPTO_ALL'])
            for period in ['all','pre2023','post2023']:
                if t.empty:
                    continue
                selected = t if period == 'all' else t[t.snapshot_period.eq(period)&~t.cross_cut]
                for (minute, target), g in selected.groupby(['reclaim_minutes','exit_mode']):
                    signal = g[g.population.eq('signal')]
                    closed = signal[signal.closed]
                    if study == 'limit-reclaim':
                        paired = match[match.reclaim_minutes.eq(minute)&match.exit_mode.eq(target)]
                        if period != 'all':
                            paired = paired[paired.entry_period.eq(period)&~paired.cross_cut]
                        sig_bp, ctl_bp = paired.signal_return_bp, paired.control_mean_return_bp
                    else:
                        paired = pairs(g)
                        sig_bp = paired.return_pct*100 if len(paired) else []
                        ctl_bp = paired.control_return_pct*100 if len(paired) else []
                    for group in groups:
                        key = (study,group,period,int(minute),target)
                        assets[key].add(symbol)
                        totals[key]['filled_n'] += len(signal)
                        totals[key]['censored_n'] += int((~signal.closed).sum())
                        add(key,'bp_',moments(closed.snapshot_bp))
                        add(key,'gross_',moments(closed.snapshot_gross_bp))
                        totals[key]['fee_bp_sum'] += float(closed.snapshot_fee_bp.sum())
                        totals[key]['matched_n'] += len(paired)
                        totals[key]['matched_signal_bp_sum'] += float(np.sum(sig_bp))
                        totals[key]['matched_control_bp_sum'] += float(np.sum(ctl_bp))
                        if study == 'ma50-retest':
                            add(key,'r_',moments(closed.return_r))
                            totals[key]['matched_signal_r_sum'] += float(paired.return_r.sum()) if len(paired) else 0.
                            totals[key]['matched_control_r_sum'] += float(paired.control_return_r.sum()) if len(paired) else 0.
            checks.append(dict(study=study,symbol=symbol,rows=len(raw),row_count_and_return_algebra=True))
            if number % 100 == 0 or number == len(records):
                print(json.dumps(dict(study=study,processed=number,total=len(records))),flush=True)

    output=[]
    for key, m in sorted(totals.items()):
        study, group, period, minutes, target = key
        bp=describe({k:m['bp_'+k] for k in ['n','wins','losses','total','win_sum','loss_sum']})
        gross=describe({k:m['gross_'+k] for k in ['n','wins','losses','total','win_sum','loss_sum']})
        n, matched = bp['n'], int(m['matched_n'])
        row=dict(study=study,group=group,period=period,confirm_minutes=minutes,target=target,
                 basis='gross' if group=='XAUUSD' else 'net',symbols_with_rows=len(assets[key]),
                 filled_n=int(m['filled_n']),closed_n=n,censored_n=int(m['censored_n']),
                 wins=bp['wins'],losses=bp['losses'],flat=bp['flat'],win_rate=bp['win_rate'],
                 mean_bp=bp['mean'],payoff_notional=bp['payoff'],pf_notional=bp['profit_factor'],
                 mean_winner_bp=bp['mean_win'],mean_loser_bp=bp['mean_loss'],
                 gross_win_rate=gross['win_rate'],mean_gross_bp=gross['mean'],
                 mean_fee_bp=m['fee_bp_sum']/n if n and group!='XAUUSD' else None,
                 matched_n=matched,
                 matched_signal_mean_bp=m['matched_signal_bp_sum']/matched if matched else None,
                 matched_control_mean_bp=m['matched_control_bp_sum']/matched if matched else None)
        if study=='ma50-retest':
            r=describe({k:m['r_'+k] for k in ['n','wins','losses','total','win_sum','loss_sum']})
            row.update(mean_r=r['mean'],total_r=m['r_total'],payoff_r=r['payoff'],pf_r=r['profit_factor'],
                       matched_signal_mean_r=m['matched_signal_r_sum']/matched if matched else None,
                       matched_control_mean_r=m['matched_control_r_sum']/matched if matched else None)
        output.append(row)
    dest=manifest_path.parent
    pd.DataFrame(output).to_csv(dest/'metrics.csv',index=False)
    summary=dict(captured_at=manifest['captured_at'],status='partial_snapshot',
                 completed_assets={k:len(v) for k,v in manifest['studies'].items()},
                 source_coverage=coverage,metrics=output,checks=checks,
                 validation_scope='Receipt row counts, unique trade IDs and native saved return algebra only; full fills reconciliation remains queued.',
                 pairing_scope='Existing per-study complete closed 1+3 matches; conditional subset, not every opportunity.',
                 return_scope='Equal-weight independent unlevered per-trade notional means; R separately named. No account return or annualization.',
                 inference='Descriptive snapshot only; no new significance inference or parameter selection.',
                 execution_replays=0)
    (dest/'summary.json').write_text(json.dumps(finite(summary),ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest')
    run(parser.parse_args().manifest)
