"""Describe the frozen altcoin case4 N5 ledger; never simulate new trades.

Source: native VectorBT open-priority ledger and actual-entry-week controls.
All calculations use recorded fills, costs and times. Realized statistics may
use exit data; they are retrospective diagnostics, never signal features.
Coin streaks use entry order and censored rows break the sequence. Market
streaks use closed-trade exit intervals; all permutations within a shared 5m
interval are bounded because intrabar ordering is not observed.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_parameter_analysis import add_actual_week, strict_pairs

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-monday-vectorbt-parameters-20261023-v1'
OUT = EXP / 'alt-case4-n5-detail-20261024'
CONFIG = 'case4-n5-v20'


def streak_bounds(blocks):
    """Exact min/max longest True run over all within-block permutations."""
    blocks = [(int(s), int(f)) for s, f in blocks]
    tail = maximum = 0
    for s, f in blocks:
        maximum = max(maximum, tail + s)
        tail = tail + s if f == 0 else s

    def feasible(cap):
        tail = 0
        for s, f in blocks:
            tail = tail + s if f == 0 else max(0, s - f * cap + tail)
            if tail > cap:
                return False
        return True

    lo, hi = 0, maximum
    while lo < hi:
        mid = (lo + hi) // 2
        if feasible(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo, maximum


def longest(mask):
    best = tail = 0
    for value in mask:
        tail = tail + 1 if value else 0
        best = max(best, tail)
    return best


def check_bounds():
    # Compare the analytical bounds with every ordering of small tie blocks.
    options = [(s, f) for s in range(4) for f in range(4) if 0 < s + f <= 4]
    checked = 0
    for blocks in itertools.product(options, repeat=2):
        perms = [set(itertools.permutations([True] * s + [False] * f)) for s, f in blocks]
        values = [longest(a + b) for a, b in itertools.product(*perms)]
        assert streak_bounds(blocks) == (min(values), max(values)), blocks
        checked += 1
    return checked


def ratio(a, b):
    return float(a / b) if b and np.isfinite(b) else None


def stats(g):
    a = g[g.population.eq('signal')]
    c = a[a.closed]
    r = c.net_r
    w, loss = r[r > 0], r[r < 0]
    pairs = strict_pairs(g, 'net')
    mean_win, mean_loss = w.mean(), -loss.mean()
    return dict(signals=len(a), closed=len(c), censored=len(a) - len(c),
        symbols=int(a.symbol.nunique()), wins=len(w), losses=len(loss),
        flat=int(r.eq(0).sum()), win_rate=ratio(len(w), len(c)),
        mean_win_r=float(mean_win) if len(w) else None,
        mean_loss_r=float(-mean_loss) if len(loss) else None,
        realized_payoff=ratio(mean_win, mean_loss),
        breakeven_win_rate=ratio(mean_loss, mean_win + mean_loss),
        profit_factor=ratio(w.sum(), -loss.sum()),
        mean_gross_r=float(c.gross_r.mean()), mean_fee_r=float(c.fee_r.mean()),
        mean_net_r=float(r.mean()), total_gross_r=float(c.gross_r.sum()),
        total_fee_r=float(c.fee_r.sum()), total_net_r=float(r.sum()),
        positive_r_sum=float(w.sum()), negative_r_sum=float(loss.sum()),
        mean_net_bp=float(c.net_bp.mean()), median_net_r=float(r.median()),
        take_profit=int(c.reason.eq(4).sum()), stop_loss=int(c.reason.eq(1).sum()),
        take_profit_net_loss=int((c.reason.eq(4) & r.lt(0)).sum()),
        fee_flipped_winners=int((c.gross_r.gt(0) & r.lt(0)).sum()),
        planned_rr_below_1=int(c.planned_rr.lt(1).sum()),
        paired=len(pairs), paired_signal_r=float(pairs.net_r.mean()) if len(pairs) else None,
        paired_random_r=float(pairs.control_net_r.mean()) if len(pairs) else None,
        paired_excess_bp=float((pairs.net_bp - pairs.control_net_bp).mean()) if len(pairs) else None)


def runs(g, column, order, scope):
    a = g.sort_values(order).reset_index(drop=True)
    mask = a.closed & (a.reason.eq(1) if column == 'stop' else a.net_r.lt(0))
    rows = []
    start = None
    for i, yes in enumerate([*mask.tolist(), False]):
        if yes and start is None:
            start = i
        elif not yes and start is not None:
            x = a.iloc[start:i]
            rows.append(dict(scope=scope, kind=column, count=len(x),
                first_trade_id=x.iloc[0].trade_id, last_trade_id=x.iloc[-1].trade_id,
                first_entry_bj=x.iloc[0].entry_bj, last_entry_bj=x.iloc[-1].entry_bj,
                first_exit_bj=x.iloc[0].exit_start_bj, last_exit_bj=x.iloc[-1].exit_start_bj,
                net_r=float(x.net_r.sum()), symbol_count=int(x.symbol.nunique()),
                trade_ids='|'.join(x.trade_id.astype(str))))
            start = None
    return rows


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    source = Path(__file__).relative_to(ROOT)
    subprocess.run(['git', 'ls-files', '--error-unmatch', str(source)], cwd=ROOT,
                   stdout=subprocess.DEVNULL, check=True)
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', str(source)], cwd=ROOT, check=True)
    tests = check_bounds()
    path = EXP / 'all-trades-open-priority.csv.gz'
    parts = []
    for chunk in pd.read_csv(path, chunksize=200000, float_precision='round_trip'):
        parts.append(chunk[chunk.config_id.eq(CONFIG) & ~chunk.symbol.isin(['BTCUSDT', 'ETHUSDT', 'XAUUSD'])])
    f = add_actual_week(pd.concat(parts, ignore_index=True))
    f['planned_rr'] = f.side * (f.target_price - f.entry) / f.risk
    f['risk_bp'] = f.risk / f.entry * 10000
    for col, name in [('entry_time', 'entry_bj'), ('pin_time', 'pin_bj'),
                      ('exit_interval_start', 'exit_start_bj'), ('exit_interval_end', 'exit_end_bj')]:
        f[name] = pd.to_datetime(f[col], utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    f['holding_h_lower'] = (pd.to_datetime(f.exit_interval_start, utc=True) - pd.to_datetime(f.entry_time, utc=True)).dt.total_seconds() / 3600
    f['holding_h_upper'] = (pd.to_datetime(f.exit_interval_end, utc=True) - pd.to_datetime(f.entry_time, utc=True)).dt.total_seconds() / 3600
    a = f[f.population.eq('signal')].copy()
    c = a[a.closed].copy()
    assert len(a) == 9065 and len(c) == 9041 and c.net_r.gt(0).sum() == 5578
    assert not a.trade_id.duplicated().any()
    assert set(c.reason) == {1, 4} and set(a[~a.closed].reason) == {0}
    np.testing.assert_allclose(c.net_r, c.gross_r - c.fee_r, atol=1e-12)
    metrics = pd.read_csv(EXP / 'metrics-entry-week.csv')
    ref = metrics[(metrics.config_id == CONFIG) & (metrics.group == 'ALTCOINS') & (metrics.period == 'all')].iloc[0]
    all_stats = stats(f)
    np.testing.assert_allclose([all_stats['mean_net_r'], all_stats['win_rate'], all_stats['profit_factor']],
                               [ref.mean_r, ref.win_rate, ref.profit_factor], atol=1e-12)
    assert all_stats['paired'] == ref.paired
    np.testing.assert_allclose([all_stats['paired_signal_r'], all_stats['paired_random_r']],
                              [ref.paired_signal_r, ref.paired_random_r], atol=1e-12)
    all_runs = []
    tie_bounds = {}
    for kind in ('stop', 'loss'):
        all_runs += runs(c, kind, ['exit_interval_start', 'symbol', 'trade_id'], 'market_exit_order')
        mask = c.reason.eq(1) if kind == 'stop' else c.net_r.lt(0)
        blocks = c.assign(flag=mask).groupby('exit_interval_start').flag.agg(['sum', 'count'])
        minimum, maximum = streak_bounds(zip(blocks['sum'], blocks['count'] - blocks['sum']))
        tie_bounds[kind] = dict(minimum=minimum, maximum=maximum)
        for symbol, coin in a.groupby('symbol'):
            all_runs += runs(coin, kind, ['entry_time', 'trade_id'], 'coin_entry_order:' + symbol)
            all_runs += runs(coin, kind, ['exit_interval_start', 'trade_id'], 'coin_exit_order:' + symbol)
    run_frame = pd.DataFrame(all_runs).sort_values(['count', 'scope', 'first_entry_bj'], ascending=[False, True, True])
    longest_runs = {}
    for prefix in ('market_exit_order', 'coin_entry_order:', 'coin_exit_order:'):
        for kind in ('stop', 'loss'):
            best = run_frame[run_frame.scope.str.startswith(prefix) & run_frame.kind.eq(kind)].iloc[0]
            longest_runs[prefix + kind] = best.drop('trade_ids').to_dict()
    quantiles = []
    for metric in ('planned_rr', 'risk_bp', 'gross_r', 'fee_r', 'net_r', 'net_bp', 'holding_h_lower', 'holding_h_upper'):
        quantiles.append(dict(metric=metric, **{'p' + str(round(q * 100)): float(c[metric].quantile(q))
                             for q in (0, .01, .05, .25, .5, .75, .95, .99, 1)}))
    rows = []
    for name, g in [('all', f), ('before2023', f[f.segment.eq(0) & ~f.crosses_selection_cut]),
                    ('from2023', f[f.segment.eq(1) & ~f.crosses_selection_cut])]:
        rows.append(dict(dimension='period', value=name, **stats(g)))
    for dim, key in [('side', f.side.map({1: 'long', -1: 'short'})),
                     ('entry_year_utc', pd.to_datetime(f.entry_time, utc=True).dt.year),
                     ('entry_month_utc', pd.to_datetime(f.entry_time, utc=True).dt.strftime('%Y-%m')),
                     ('entry_hour_bj', pd.to_datetime(f.entry_time, utc=True).dt.tz_convert('Asia/Shanghai').dt.hour),
                     ('symbol', f.symbol)]:
        for value, g in f.groupby(key):
            if g.population.eq('signal').any():
                rows.append(dict(dimension=dim, value=str(value), **stats(g)))
    # Controls keep their own actual entry timestamp in subgroup comparisons.
    # This is stricter than the all-period pair match; paired denominators vary.
    OUT.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows)
    table.to_csv(OUT / '分组统计.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(quantiles).to_csv(OUT / '分布与持仓时间.csv', index=False, encoding='utf-8-sig')
    run_frame.to_csv(OUT / '连续止损及连亏区间.csv', index=False, encoding='utf-8-sig')
    a['direction_cn'] = a.side.map({1: '多', -1: '空'})
    a['exit_reason_cn'] = a.reason.map({0: '未决/数据边界', 1: '原插针止损', 4: '近侧周一边界全仓止盈'})
    a['outcome_cn'] = np.where(~a.closed, '未决', np.where(a.net_r.gt(0), '盈利', np.where(a.net_r.lt(0), '亏损', '持平')))
    front = ['trade_id', 'symbol', 'direction_cn', 'closed', 'outcome_cn', 'exit_reason_cn',
             'pin_bj', 'entry_bj', 'exit_start_bj', 'exit_end_bj', 'entry', 'stop', 'target_price',
             'exit_price', 'planned_rr', 'risk_bp', 'gross_r', 'fee_r', 'net_r', 'net_bp',
             'holding_h_lower', 'holding_h_upper']
    a = a.sort_values(['entry_time', 'symbol', 'trade_id'])
    a[front + [col for col in a if col not in front]].to_csv(OUT / '全部9065笔交易.csv', index=False, encoding='utf-8-sig')
    max_market = run_frame[(run_frame.scope == 'market_exit_order') & (run_frame.kind == 'stop')].iloc[0]
    ids = max_market.trade_ids.split('|')
    a.set_index('trade_id').loc[ids].reset_index().to_csv(OUT / '最长市场连续止损逐笔.csv', index=False, encoding='utf-8-sig')
    result = dict(configuration=CONFIG, generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source=str(path.relative_to(ROOT)), source_bytes=path.stat().st_size,
        source_mtime_ns=path.stat().st_mtime_ns, no_engine_replay=True,
        rules=dict(pin='First weekly directional touch; wick-only 1.618 touch with body strictly inside',
            confirmation='Five consecutive 4h closes inside/on level; wicks/opens unrestricted',
            entry='Next 4h open, 24h after pin open; market/taker0.0005',
            stop='Original pin extreme, stop market/taker0.0005',
            target='Long Monday low; short Monday high; fixed full-position limit/maker0.0002',
            maximum_holding=None, partial=False, breakeven=False, trailing=False,
            anchor='Completed Monday America/New_York; 4h bars on UTC grid'),
        all=all_stats, first_entry_bj=a.entry_bj.min(), last_entry_bj=a.entry_bj.max(),
        quantiles=quantiles, longest_runs=longest_runs, within_exit_interval_streak_bounds=tie_bounds,
        same_coin_same_entry_time_rows=int(a.duplicated(['symbol', 'entry_time'], keep=False).sum()),
        ambiguous_stop_target_bars=int(c.ambiguous_bar.sum()),
        split_crossing_closed=int(c.crosses_selection_cut.sum()),
        engines=a.engine_origin.value_counts().to_dict(), validation=dict(exhaustive_tie_test_cases=tests,
            canonical_counts_and_metrics=True, fee_reconciliation=True, unique_trade_ids=True),
        limitations=['Independent fixed-initial-risk R; not a funded shared account or a compounded equity curve.',
            'Market streak uses completed exits. Within the same 5m interval ordering is unknown; report exact bounds.',
            'Coin entry-order streak counts only known stop/loss outcomes. Censored trades break a streak.',
            'No slippage, funding, liquidity, liquidation or account capital constraints. Binance prices with stated OKX costs.',
            'History and winning configuration are previously explored; full-period highest win rate is post-hoc.',
            'MFE/MAE is not present in this frozen N5 ledger and is not invented by this descriptive summary.',
            'Subgroup controls use actual timestamps in that subgroup; paired denominators may shrink.'],
        training_eligible=False, production_eligible=False)
    dump(OUT / 'summary.json', result)
    definitions = dict(R='Initial entry-to-original-stop price distance. All net R use the recorded per-fill fees.',
        win_rate='Net positive closed signals / all closed signals; excludes 24 censored.',
        realized_payoff='Mean net winning R / absolute mean net losing R; not planned RR and not profit factor.',
        profit_factor='Sum net winning R / absolute sum net losing R.',
        holding_h_lower='Entry open to start of exit 5m interval; true exit may occur up to interval end.',
        planned_rr='Entry-to-near-Monday-target distance divided by initial stop distance; before fees.',
        reason='0=censored,1=stop,4=target. TP hit need not be net profitable.',
        paired='Three closed controls per signal, same symbol, actual NY entry week, side, volatility bucket.',
        censored='Retained in all-trade CSV; never counted as realized profit/loss or completed exit.',
        streak='Full details in summary; trade_ids separated by |; timestamps explicitly Beijing/UTC.',
        reproduction='/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_alt_n5_stats')
    dump(OUT / '字段定义.json', definitions)
    with zipfile.ZipFile(OUT / '山寨4h五根确认完整统计.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.iterdir()):
            if p.suffix in ('.csv', '.json'):
                z.write(p, p.name)
    print(json.dumps({k: result[k] for k in ('all', 'longest_runs', 'within_exit_interval_streak_bounds', 'quantiles', 'validation')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
