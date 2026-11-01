"""1000U capital path of the touch-close survivor slice (owner 2026-11-01).

Owner: "先做一次详细的回测啊 用1000u". The survivor slice of exp-monday-limit-close-slices-20261101-v1 (frozen touch-bar
score >= 0.5, the touch 5m bar closed still beyond the Monday 1.618 having recovered < 0.64 of the excursion; market
entry at that close) beat its matched random-time entries in exp-monday-limit-close-slice-control-20261101-v1. This
writes one continuous 1000U account per target and start from that run's saved NautilusTrader signal rows (B30, equal
to the 10-28 close-reclaim fills); no new fills, scores or parameters. Per trade: worse of the two intrabar paths,
taker entry + 10bp entry slippage, 10bp on non-target exits (fees in the framework fills). Sizing is the frac N20 3x
of every replay of the slice so far (monday_limit_return_opt.frac: margin = realized equity / 20 per fill, at most 20
open, margin in use <= equity, liquidation when the MAE reaches the 3x line), one tie seed: the first of return-opt.
Starts 2020-01-01 (the slice was chosen on 2020-2024, so that stretch is in sample) and 2025-01-01 (after it).
The ledger adds each trade's prices, exit reason and touch-bar facts; the booklet projection is
strategy_booklet_metrics.project. Equity is realized only (no open P&L), funding is not modelled.
Config options added for the owner's follow-up "有尝试过跟之前的all3策略一样去弄吗": `allocator` "allin" replays the
allin3 paper rule (monday_limit_langlang.path_run: one trade at a time with the whole account at `leverage`, a fill whose
MAE reaches the liquidation line loses the account, below 100U a blow-up refilled with a new 1000U stake that is cash
added, not profit) plus the monthly-start statistics of the allin3 studies (start on the first of each month from
first_start to last_start, several tie seeds, share reaching 10x / 100x of the stakes, days, blow-ups); `fills` reads the
signal rows from another run with the same B30 fills (the 10-28 close-reclaim run holds all five targets).
Owner "改成这个" (the stop 20 percent from the entry): `variant` picks the signal rows of another stop from
exp-monday-limit-close-stop-grid-20261101-v1 (e.g. E20); default B30.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.strategy_booklet_paths import Trace, dated
from yoyo.evaluation.strategy_booklet_metrics import project

A = ROOT/'experiments/active'
# Exit codes of nautilus_independent for this policy (market entry, target, -30 percent backstop, 60m reclaim).
REASONS = {4: 'target', 11: 'not_reclaimed_60m', 12: 'reclaimed_inside_breakeven', 5: 'breakeven_stop', 1: 'backstop',
           2: 'stop_after_arm', 14: 'close_exit'}


def symbol_rows(args):
    """Signal rows of one symbol with the exit price of the chosen (worse) path and the 1.618 level."""
    exp, symbol, slip_entry, slip_exit, variant = args
    _, d, issues = cr.load_symbol((exp, symbol, slip_entry, slip_exit))
    if d.empty:
        return d, issues
    d = d[d.variant == variant].copy()
    key = pd.MultiIndex.from_arrays([d.event, d['mode']])
    px = {}
    for p in ('ohlc', 'olhc'):
        t = pd.read_csv(Path(exp)/p/'symbols'/symbol/'trades.csv.gz')
        t = t[t.variant == variant]
        parts = t.trade_id.str.split(':')
        px[p] = t.set_index([parts.str[:3].str.join(':'), parts.str[4]])
    o, l = px['ohlc'].reindex(key), px['olhc'].reindex(key)
    d['trade_id'] = o.trade_id.to_numpy()
    ok = (o.reason.to_numpy() == d.reason.to_numpy()) & (pd.to_datetime(o.exit_time, utc=True).to_numpy() == d.exit_t.to_numpy())
    d['exit_price'] = np.where(ok, o.exit_price, l.exit_price)
    lv = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'level'])
    lv = lv[(lv.population == 'signal') & (lv.reclaim_minutes == 60) & (lv.exit_mode == 'near_monday_edge')]
    lv = lv.assign(event=symbol+':'+lv.week+':'+np.where(lv.side == 1, '+1', '-1')).drop_duplicates('event')
    d['level'] = d.event.map(lv.set_index('event').level)
    return d, issues


def allin(x, cfg, seed, trace):
    """allin3 rule on one path, plus monthly starts as the allin3 studies (days to 10x / 100x of the stakes)."""
    from yoyo.evaluation.monday_limit_langlang import path_run
    path_run(x, 'allin', cfg['leverage'], seed, trace=trace)
    rows = trace.rows[1:]
    out = dict(blowups=int(sum(r['cashflow'] > 0 for r in rows)), cash_added=float(sum(r['cashflow'] for r in rows)),
               taken=int(sum(r['notional'] > 0 for r in rows)), months={})
    m = cfg['monthly']
    for start in pd.date_range(m['first_start'], m['last_start'], freq='MS', tz='UTC'):
        y = x[x.entry_t >= start].reset_index(drop=True)
        for k in range(m['ties']):
            out['months'][f'{start.date()}|{k}'] = path_run(y, 'allin', cfg['leverage'], m['seed']+k)
    d = pd.DataFrame([{**{f'd{q}': (h[q]['days'] if h[q] else np.nan) for q in (10, 100)},
                       **{f'b{q}': (h[q]['blowups'] if h[q] else np.nan) for q in (10, 100)}} for h in out['months'].values()])
    out['monthly_starts'] = {f'x{q}': dict(reached_pct=float(100*d[f'd{q}'].notna().mean()),
                                           days_median=float(d[f'd{q}'].median()) if d[f'd{q}'].notna().any() else None,
                                           blowups_before_median=float(d[f'b{q}'].median()) if d[f'b{q}'].notna().any() else None)
                             for q in (10, 100)}
    out['starts'] = int(len(d)); out.pop('months')
    return out


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
    from yoyo.evaluation.monday_limit_return_opt import frac
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_survivor_paths.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['source']
    surv = pd.read_csv(src/'survivors.csv.gz').set_index('event')
    fills = ROOT/cfg.get('fills', cfg['source'])
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(fills), s, slip['market_entry'], slip['non_target_exit'], cfg.get('variant', 'B30'))
                                        for s in sorted(set(surv.symbol))], chunksize=4))
    t = pd.concat([g[0] for g in got if len(g[0])], ignore_index=True)
    issues = [i for g in got for i in g[1]]
    t = t[~t.event.isin(BAD_EVENTS) & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC')) & t.event.isin(surv.index)].copy()
    t['score'] = t.event.map(surv.score); t['recovery'] = t.event.map(surv.recovery)
    t['symbol'] = t.event.str.split(':').str[0]
    t['beyond_pct'] = 100*t.side*(t.level-t.entry)/t.level
    t['reason_name'] = t.reason.map(REASONS).fillna(t.reason.astype(str))
    if cfg.get('max_beyond_pct') is not None:
        # The level-based -30 percent stop could not be placed when the close was already 30 percent past the level, so
        # the original rule never traded those collapses; an entry-based stop needs the same filter made explicit.
        manifest_excluded = sorted(t.event[t.beyond_pct >= cfg['max_beyond_pct']].unique())
        t = t[t.beyond_pct < cfg['max_beyond_pct']].copy()
    else:
        manifest_excluded = []
    seed = json.loads((ROOT/cfg['tie_seed_from']).read_text())['seed']
    manifest = dict(rows=int(len(t)), events=int(t.event.nunique()), issues=issues[:20], seed=seed, accounts={},
                    excluded_beyond=manifest_excluded)
    saved = json.loads((src/'summary.json').read_text())['results']['plans']
    for mode in cfg['targets']:
        for start in cfg['starts']:
            x = dated(t[(t['mode'] == mode) & (t.entry_t >= pd.Timestamp(start, tz='UTC'))]).reset_index(drop=True)
            trace = Trace(pd.Timestamp(start, tz='UTC').value)
            if cfg.get('allocator', 'frac') == 'allin':
                result = allin(x, cfg, seed, trace)
            else:
                result = frac(x, cfg['slots'], cfg['leverage'], seed, trace=trace)
            frame = pd.DataFrame(trace.rows)
            error = float(np.abs(frame.equity-(1000+frame.pnl.cumsum()+frame.cashflow.cumsum())).max())
            if error > 1e-6*max(1., frame.equity.max()):
                raise ValueError(f'{mode} {start}: cash-flow reconciliation {error}')
            facts = x.rename(columns={'trade_id': 'fill_id'}).set_index('event')[['fill_id', 'entry_t', 'exit_t', 'entry', 'exit_price', 'level', 'beyond_pct', 'score',
                                          'recovery', 'reason_name', 'mae_pct']]
            ledger = frame.join(facts, on='trade_id')
            ledger['margin'] = ledger.notional/cfg['leverage']
            name = f"{mode}-{start[:4]}"
            ledger.to_csv(exp/f'{name}-ledger.csv.gz', index=False, compression='gzip')
            meta = dict(target=mode, start=start, end=str(x.exit_t.max()), seed=seed, candidates=int(len(x)),
                        funded=int((frame.notional > 0).sum()),
                        allocator=dict(kind=cfg.get('allocator', 'frac'), slots=cfg.get('slots', 1), leverage=cfg['leverage']),
                        allocator_result=result, reconciliation_error=error)
            data = project(frame, {**meta, 'note': ''})
            (exp/f'{name}-history.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
                                                               default=lambda v: v.item()))
            by_year = ledger[ledger.notional > 0].assign(year=lambda y: pd.to_datetime(y.time_ms, unit='ms', utc=True).dt.year)
            years = {str(k): dict(trades=int(len(g)), pnl=float(g.pnl.sum()), win_pct=float(100*(g.pnl > 0).mean()),
                                  end=float(g.equity.iloc[-1])) for k, g in by_year.groupby('year')}
            reasons = {k: dict(n=int(len(g)), mean_bp=float(g.net_bp.mean()), pnl=float(g.pnl.sum()))
                       for k, g in ledger[ledger.notional > 0].groupby('reason_name')}
            # The control run replayed 2020 onward with 10 tie seeds; a 2025 start has no saved counterpart.
            ref = (saved.get(f'{mode}|signal', {}).get('all', {}).get('replay')
                   if start == '2020-01-01' and cfg.get('allocator', 'frac') == 'frac' and cfg.get('variant', 'B30') == 'B30' else None)
            manifest['accounts'][name] = {**meta, 'summary': data['summary'], 'years': years, 'reasons': reasons,
                                          'saved_median_of_ties': ref}
            print(f"{name}: {meta['funded']}/{meta['candidates']} funded, end {data['summary']['end']:.2f}, "
                  f"dd {data['summary']['max_drawdown_pct']:.1f}, win {data['summary']['win_pct']:.1f}", flush=True)
    manifest.update(source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    training_eligible=False, production_eligible=False)
    (exp/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=lambda v: v.item() if hasattr(v, 'item') else str(v)))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    run(a.config, a.workers)
