"""Export per-symbol views of the frozen two-month SPIKE retest experiment.

Source: exp-spike-v128-retest-entry-20260923-v1/summary_v1. No strategy replay,
parameter selection, or new market data. Features are not constructed; all
outcome columns are retrospective labels from the frozen ledgers. Empty streams
remain visible, censored trades are excluded from returns, and random controls
use the original same-policy closed-fill matching rather than zero imputation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_retest_report as report
from yoyo.evaluation.spike_v8_six_filters import _committed


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(experiment: Path, output: Path):
    source = Path(__file__)
    if not _committed([source]):
        raise ValueError('Commit the exporter before deriving artifacts')
    folder = experiment / 'summary_v1'
    receipt = json.loads((folder / 'summary_receipt.json').read_text())
    cfg = receipt['config']
    run = experiment / 'run_v1'
    if digest(run / 'manifest.json') != receipt['run_manifest_sha256']:
        raise ValueError('Run manifest changed')
    manifest = json.loads((run / 'manifest.json').read_text())
    identity = json.loads((run / 'identity.json').read_text())
    if not manifest['complete'] or manifest['errors'] or identity['subset']:
        raise ValueError('Incomplete source universe')
    frames = {}
    for name in ['candidate_outcomes', 'serial_trades', 'serial_statuses', 'controls', 'summary']:
        filename = name + ('.csv' if name == 'summary' else '.csv.gz')
        if digest(folder / filename) != receipt['files'][filename]:
            raise ValueError(f'Source hash changed: {filename}')
        frame = pd.read_csv(folder / filename, low_memory=False)
        for column in ['anchor_close', 'decision_time', 'entry_time', 'exit_time',
                       'control_exit_time', 'control_decision_time']:
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], utc=True)
        for column in ['matched', 'censored', 'control_censored']:
            if name != 'summary' and column in frame:
                frame[column] = report.stats.boolean(frame[column])
        if name != 'summary':
            key = ['trade_key', 'control_pool' if name == 'controls' else 'policy']
            if frame.duplicated(key).any():
                raise ValueError(f'Duplicate source identity: {name}')
        frames[name] = frame
    groups = {name: {key: part for key, part in frame.groupby(['symbol', 'timeframe_min', 'arm'])}
              for name, frame in frames.items() if name != 'summary'}
    split = pd.Timestamp(cfg['split'])
    records = []
    keep_metrics = ['closed', 'wins', 'win_rate', 'mean_net_r', 'median_net_r',
                    'mean_net_bp', 'mean_gross_bp', 'sum_net_r', 'pf_r',
                    'best_net_r', 'worst_net_r', 'net_ge5r', 'net_ge10r']
    for symbol in identity['symbols']:
        for minutes in cfg['timeframes']:
            stream = f'{symbol}_{minutes}m'
            path = run / 'streams' / stream / 'receipt.json'
            if digest(path) != manifest['receipts'][stream]:
                raise ValueError(f'Stream receipt changed: {stream}')
            stream_summary = json.loads(path.read_text())['summary']
            for arm in ['v9_both', 'joint']:
                key = (symbol, minutes, arm)
                ca, tr, st, co = [groups[n].get(key, frames[n].iloc[:0]) for n in
                                 ['candidate_outcomes', 'serial_trades', 'serial_statuses', 'controls']]
                for policy in ['baseline', 'retest']:
                    c, t, s = [frame[frame.policy == policy] for frame in [ca, tr, st]]
                    closed = t[t.status == 'closed']
                    metrics = report.stats.extended_metrics(closed)
                    random = report.random_result(t, co, policy, 'all', split, cfg)
                    row = dict(symbol=symbol, timeframe_min=minutes, arm=arm, policy=policy,
                               candidates=len(c), confirmations=(len(c) if policy == 'baseline'
                               else int(c.confirmation_i.notna().sum())), taken=len(t),
                               censored=len(t) - len(closed), skipped_in_position=int(s.status.eq('skipped_in_position').sum()),
                               last_entry_beijing=(t.entry_time.max().tz_convert('Asia/Shanghai').isoformat() if len(t) else ''),
                               window_bars_actual=stream_summary['window_bars_actual'],
                               window_bars_expected=stream_summary['window_bars_expected'],
                               valid_ready_window_bars=stream_summary['valid_ready_window_bars'],
                               window_gap_count=stream_summary['window_gap_count'])
                    row.update({k: metrics.get(k, np.nan) for k in keep_metrics})
                    row.update(random)
                    for period in ['earlier', 'later', 'cross_split']:
                        part = report.cohort(t, period, split, closed=True)
                        row.update({period + '_closed': len(part), period + '_mean_net_r': part.net_r.mean(),
                                    period + '_mean_net_bp': part.net_bp.mean()})
                    records.append(row)
    result = pd.DataFrame(records)
    checks = []
    for key, rows in result.groupby(['timeframe_min', 'arm', 'policy']):
        minutes, arm, policy = key
        expected = frames['summary'].query('timeframe_min == @minutes and arm == @arm and policy == @policy and period == "all"').iloc[0]
        for field in ['candidates', 'taken', 'censored', 'closed', 'wins', 'net_ge5r', 'net_ge10r', 'matched']:
            assert rows[field].sum() == expected[field], (key, field)
        for metric in ['mean_net_r', 'mean_net_bp', 'mean_gross_bp', 'win_rate']:
            actual = (rows[metric].fillna(0) * rows.closed).sum() / rows.closed.sum()
            assert np.isclose(actual, expected[metric], rtol=1e-10, atol=1e-10), (key, metric)
        paired = (rows.mean_excess_bp.fillna(0) * rows.matched).sum() / rows.matched.sum()
        assert np.isclose(paired, expected.mean_excess_bp, rtol=1e-10, atol=1e-10), key
        checks.append(dict(timeframe_min=int(minutes), arm=arm, policy=policy, closed=int(rows.closed.sum()), matched=int(rows.matched.sum())))
    index = ['symbol', 'timeframe_min', 'arm', 'window_bars_actual', 'window_bars_expected', 'valid_ready_window_bars', 'window_gap_count']
    fields = ['candidates', 'confirmations', 'taken', 'closed', 'censored', 'win_rate', 'mean_net_r',
              'mean_net_bp', 'sum_net_r', 'net_ge5r', 'matched', 'random_mean_net_bp',
              'paired_target_mean_net_bp', 'mean_excess_bp', 'later_closed', 'later_mean_net_r', 'later_mean_net_bp']
    comparison = result.pivot(index=index, columns='policy', values=fields)
    comparison.columns = [f'{policy}_{metric}' for metric, policy in comparison.columns]
    comparison = comparison.reset_index()
    comparison['delta_mean_net_bp'] = comparison.retest_mean_net_bp - comparison.baseline_mean_net_bp
    comparison['delta_mean_net_r'] = comparison.retest_mean_net_r - comparison.baseline_mean_net_r
    translations = dict(symbol='合约', timeframe_min='周期分钟', arm='模式', window_bars_actual='实际K线数',
                        window_bars_expected='窗口理论K线数', valid_ready_window_bars='预热就绪K线数', window_gap_count='数据断档数',
                        delta_mean_net_bp='回踩减原版_平均净bp', delta_mean_net_r='回踩减原版_平均净R')
    labels = dict(candidates='原始候选数', confirmations='确认数', taken='入场数', closed='已平仓数', censored='截尾未完成数',
                  win_rate='净胜率小数', mean_net_r='平均净R', mean_net_bp='平均净bp', sum_net_r='净R合计非账户收益',
                  net_ge5r='净收益至少5R笔数', matched='随机配对笔数', random_mean_net_bp='配对随机平均净bp',
                  paired_target_mean_net_bp='配对目标平均净bp', mean_excess_bp='配对超额bp', later_closed='后段已平仓数',
                  later_mean_net_r='后段平均净R', later_mean_net_bp='后段平均净bp')
    for policy, label in [('baseline', '原版'), ('retest', '回踩')]:
        translations.update({f'{policy}_{key}': f'{label}_{value}' for key, value in labels.items()})
    comparison = comparison.rename(columns=translations)
    comparison['模式'] = comparison['模式'].map({'v9_both': '普通多空', 'joint': '联合多头'})
    output.mkdir(parents=True, exist_ok=False)
    result.to_csv(output / 'symbol_metrics.csv', index=False)
    comparison.to_csv(output / '逐币原版回踩对照.csv', index=False, encoding='utf-8-sig')
    trades = frames['serial_trades'].query('policy in ["baseline", "retest"]').copy()
    for column in ['anchor_close', 'signal_close', 'entry_time', 'exit_time']:
        trades[column] = pd.to_datetime(trades[column], utc=True).dt.tz_convert('Asia/Shanghai')
    trade_columns = ['symbol', 'timeframe_min', 'arm', 'policy', 'trade_key', 'side', 'anchor_close', 'signal_close',
                     'entry_time', 'exit_time', 'entry_price', 'initial_stop', 'exit_price', 'exit_reason', 'status',
                     'censored', 'net_r', 'net_bp', 'gross_bp']
    trades[trade_columns].sort_values(['symbol', 'timeframe_min', 'arm', 'policy', 'entry_time']).to_csv(output / 'trade_details_beijing.csv', index=False)
    evidence = dict(source_summary_receipt_sha256=digest(folder / 'summary_receipt.json'), source_config=cfg,
                    source_code={str(source): digest(source), report.__file__: digest(report.__file__), report.stats.__file__: digest(report.stats.__file__)},
                    command=f'.venv/bin/python -m yoyo.evaluation.spike_v128_retest_symbols --experiment {experiment} --output {output}',
                    symbols=len(identity['symbols']), metric_rows=len(result), comparison_rows=len(comparison), aggregate_checks=checks,
                    definitions={'venue': 'Binance USD-M archived universe; not native TradingView parity',
                                 'returns': 'Closed trades only, fixed 20bp round-trip. R uses each trade initial risk; sums are not account returns.',
                                 'random': 'Same-policy closed-fill matched subset; unmatched stays NaN. Symbol p-values exploratory, not corrected across symbols.',
                                 'periods': 'UTC 2026-08-23 split by original anchor; earlier resolved before split, crossing positions separate.',
                                 'zero_rows': 'No trade is distinct from no ready market bars; inspect per-stream coverage.',
                                 'scope': 'Retrospective aggregation only; no new backtest, model score, AUC, or top-decile selection.'},
                    files={p.name: digest(p) for p in output.iterdir()})
    (output / 'receipt.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'symbols': len(identity['symbols']), 'rows': len(result), 'checks': checks}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.experiment, args.output)
