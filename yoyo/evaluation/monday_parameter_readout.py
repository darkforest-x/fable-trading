"""Localize frozen Monday parameter results and add descriptive fill statistics.

Sources: metrics-entry-week.csv and its open-priority corrected ledger. This
module never generates signals or simulates fills. Original metrics and matched
controls remain unchanged. Realized outcomes, streaks and holding durations are
retrospective descriptions, not causal features. Period cuts match the source.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_alt_n5_stats import longest, streak_bounds

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-monday-vectorbt-parameters-20261023-v1'
OUT = ROOT / 'artifacts/monday-parameter-readout-20261024'
APP = ROOT / 'reports/monday-1618-cases-20261023/src/data.json'
GROUPS = {'BTC': 'BTC', 'ETH': 'ETH', 'XAUUSD': '现货黄金',
          'ALTCOINS': '山寨币', 'GROUP1_GROSS': 'BTC＋ETH＋黄金（毛收益汇总）'}
PERIODS = {'all': '全历史', 'earlier': '2023年前', 'later': '2023年起'}
LABELS = {
    'rank': '胜率排序序号', 'case': '情况编号', 'confirmations': '后续确认根数',
    'volume_window': '原始量能窗口字段', 'config_id': '原始配置代码',
    'config_label': '中文方案', 'group': '品种代码', 'group_label': '品种分组',
    'period': '时段代码', 'period_label': '统计时段', 'basis': '口径代码',
    'basis_label': '收益口径', 'volume_label': '实际成交量条件',
    'signals': '信号数', 'closed': '已判定笔数', 'censored': '未决笔数',
    'win_rate': '胜率比例', 'mean_r': '平均每笔R', 'total_r': '合计独立R',
    'median_r': '每笔R中位数', 'profit_factor': '盈利因子', 'mean_bp': '平均名义收益bp',
    'paired': '完整配对数', 'paired_signal_r': '配对信号平均R',
    'paired_random_r': '配对随机平均R', 'paired_excess_bp': '配对超额bp',
    'stop_first_mean_r': '同K止损优先平均R', 'cross_week_signals': '跨纽约周入场信号数',
    'wins': '盈利笔数', 'losses': '亏损笔数', 'flat': '持平笔数',
    'mean_win_r': '平均盈利R', 'mean_loss_r': '平均亏损R',
    'payoff': '实际盈亏比', 'breakeven_rate': '保本所需胜率比例',
    'mean_gross_r': '平均毛R', 'mean_fee_r': '平均手续费R',
    'tp_count': '止盈成交笔数', 'sl_count': '止损成交笔数',
    'tp_losers': '止盈成交但扣费亏损笔数', 'planned_rr_median': '计划盈亏比中位数',
    'holding_h_median': '持仓小时上界中位数', 'holding_h_max': '最长持仓小时上界',
    'first_entry': '首笔入场UTC', 'last_entry': '末笔入场UTC',
    'longest_coin_loss': '单品种入场顺序最长连亏',
    'longest_coin_sl': '单品种入场顺序最长连损',
    'market_sl_min': '市场退出顺序最长连损下界',
    'market_sl_max': '市场退出顺序最长连损上界',
}


def ratio(a, b):
    return float(a / b) if b and np.isfinite(b) else None


def main():
    source = str(Path(__file__).relative_to(ROOT))
    subprocess.run(['git', 'ls-files', '--error-unmatch', source], cwd=ROOT,
                   stdout=subprocess.DEVNULL, check=True)
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', source], cwd=ROOT, check=True)
    metrics = pd.read_csv(EXP / 'metrics-entry-week.csv', float_precision='round_trip')
    cols = ['population', 'config_id', 'symbol', 'closed', 'reason', 'segment',
            'crosses_selection_cut', 'entry_time', 'exit_interval_start', 'exit_interval_end',
            'trade_id', 'net_r', 'gross_r', 'fee_r', 'side', 'target_price', 'entry', 'risk']
    parts = []
    for chunk in pd.read_csv(EXP / 'all-trades-open-priority.csv.gz', usecols=cols,
                             chunksize=200000, float_precision='round_trip'):
        parts.append(chunk[chunk.population.eq('signal')])
    signals = pd.concat(parts, ignore_index=True)
    details = []
    for row in metrics.to_dict('records'):
        g = signals[signals.config_id.eq(row['config_id'])]
        first = g.symbol.isin(['BTCUSDT', 'ETHUSDT', 'XAUUSD'])
        if row['group'] == 'GROUP1_GROSS':
            g = g[first]
        elif row['group'] == 'ALTCOINS':
            g = g[~first]
        else:
            symbol = {'BTC': 'BTCUSDT', 'ETH': 'ETHUSDT', 'XAUUSD': 'XAUUSD'}[row['group']]
            g = g[g.symbol.eq(symbol)]
        if row['period'] != 'all':
            g = g[g.segment.eq(int(row['period'] == 'later')) & ~g.crosses_selection_cut]
        c = g[g.closed]
        r = c[f"{row['basis']}_r"]
        wins, losses = r[r > 0], r[r < 0]
        assert len(g) == row['signals'] and len(c) == row['closed']
        if len(c):
            np.testing.assert_allclose([r.mean(), r.sum(), (r > 0).mean()],
                                       [row['mean_r'], row['total_r'], row['win_rate']], atol=1e-11)
        hours = (pd.to_datetime(c.exit_interval_end, utc=True) -
                 pd.to_datetime(c.entry_time, utc=True)).dt.total_seconds() / 3600
        coin_losses, coin_sl = [], []
        for _, coin in g.groupby('symbol'):
            coin = coin.sort_values(['entry_time', 'trade_id'])
            coin_losses.append(longest(coin.closed & coin[f"{row['basis']}_r"].lt(0)))
            coin_sl.append(longest(coin.closed & coin.reason.eq(1)))
        blocks = c.assign(is_sl=c.reason.eq(1)).groupby('exit_interval_start').is_sl.agg(['sum', 'count'])
        lower, upper = streak_bounds([(x['sum'], x['count'] - x['sum']) for _, x in blocks.iterrows()])
        n, case = row['confirmations'], row['case']
        scheme = {2: f'15分钟量能插针＋{n}根实体不碰线',
                  3: f'1小时插针＋{n}根影线也不碰线',
                  4: f'4小时插针＋{n}根收盘守线'}[case]
        details.append(dict(row, config_label=scheme, group_label=GROUPS[row['group']],
            period_label=PERIODS[row['period']], basis_label='扣手续费' if row['basis'] == 'net' else '毛收益（未扣费用）',
            volume_label=f"插针量为前{row['volume_window']}根及自身最大" if case == 2 else '不使用成交量条件',
            wins=len(wins), losses=len(losses), flat=int(r.eq(0).sum()),
            mean_win_r=wins.mean(), mean_loss_r=losses.mean(),
            payoff=ratio(wins.mean(), -losses.mean()),
            breakeven_rate=ratio(-losses.mean(), wins.mean() - losses.mean()),
            mean_gross_r=c.gross_r.mean(), mean_fee_r=c.fee_r.mean() if row['basis'] == 'net' else None,
            tp_count=int(c.reason.eq(4).sum()), sl_count=int(c.reason.eq(1).sum()),
            tp_losers=int((c.reason.eq(4) & r.lt(0)).sum()),
            planned_rr_median=(c.side * (c.target_price - c.entry) / c.risk).median(),
            holding_h_median=hours.median(), holding_h_max=hours.max(),
            first_entry=g.entry_time.min() if len(g) else None,
            last_entry=g.entry_time.max() if len(g) else None,
            longest_coin_loss=max(coin_losses, default=0), longest_coin_sl=max(coin_sl, default=0),
            market_sl_min=lower, market_sl_max=upper))
    table = pd.DataFrame(details).sort_values('win_rate', ascending=False, kind='stable').reset_index(drop=True)
    table.insert(0, 'rank', range(1, len(table) + 1))
    assert len(table) == 243 and table.iloc[:16].config_id.nunique() == 5
    assert not (set(table.columns) - set(LABELS)), set(table.columns) - set(LABELS)
    OUT.mkdir(exist_ok=True)
    table.rename(columns=LABELS).to_csv(OUT / '全部243条参数统计-中文详表.csv', index=False, encoding='utf-8-sig')
    table.head(16).rename(columns=LABELS).to_csv(OUT / '截图前16条-中文详表.csv', index=False, encoding='utf-8-sig')
    rows = json.loads(table.to_json(orient='records', force_ascii=False, double_precision=15))
    ids = ['ranking-intro', 'ranking-table', 'ranking-periods', 'ranking-payoff', 'ranking-streaks', 'ranking-time']
    definitions = {**LABELS,
        'breakeven_rate': '平均亏损绝对值／（平均盈利＋平均亏损绝对值），按已观察盈亏幅度且无持平条件的保本胜率。',
        'market_sl_min': '市场已判定交易按退出5m区间排列；同区间顺序不可见，列出最长连续止损所有可能顺序的下界。',
        'market_sl_max': '同上，最长连续止损的上界；未决不构成退出事件，非资金账户回撤。',
        'longest_coin_loss': '同品种按入场时间、交易ID排序的最长连续负收益；未决和非亏损打断序列。',
        'longest_coin_sl': '同品种按入场排序的最长连续止损成交；未决及非止损打断序列。',
        'holding_h_median': '框架退出所在5m区间结束减实际入场时刻，上界最多偏长5分钟。',
        'first_entry': '所选配置、品种、时段的首笔合格信号入场UTC；不是行情文件起点。',
        'last_entry': '末笔合格信号入场UTC；不是行情文件末端。',
        'payoff': '平均正收益R／平均负收益R绝对值，使用该行net或gross口径；不同于盈利因子。'}
    source_meta = dict(label='中文参数解读与冻结成交诊断',
        files=[str((EXP / f).relative_to(ROOT)) for f in ['metrics-entry-week.csv', 'all-trades-open-priority.csv.gz', 'config.json']],
        transformation=source, original_metrics_unchanged=True, no_new_backtest=True,
        metricDefinitions=[dict(id=k, label=LABELS[k], definition=v, componentIds=ids) for k, v in definitions.items()])
    query = dict(rows=rows, source=source_meta)
    (OUT / 'reviewed-query.json').write_text(json.dumps(query, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    data = json.loads(APP.read_text())
    assert len(data['queries']['trades']['rows']) == 79430 and len(data['queries']['alt-n5-ledger']['rows']) == 9065
    data['queries']['parameter-readout'] = query
    data['buildStatus'] = 'paused'  # Separate pullback execution rules still await the owner.
    APP.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    receipt = dict(rows=len(rows), top16_unique_configs=5, top16_negative_mean=int(table.head(16).mean_r.lt(0).sum()),
        source_metrics_reconciled=243, no_new_backtest=True,
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        training_eligible=False, production_eligible=False)
    (OUT / 'validation.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
