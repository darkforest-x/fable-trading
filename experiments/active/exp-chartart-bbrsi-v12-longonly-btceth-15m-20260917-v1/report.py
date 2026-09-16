"""Render the frozen result tables from results/summary.json.

Tables are generated, not transcribed, so a number in the report cannot drift
from the number on disk. Percentages are per-trade means in basis points, the
same unit the v1.1 study reported, so the two rounds can sit in one table.
"""
from __future__ import annotations

import json
from pathlib import Path

EXP = Path(__file__).resolve().parent
ARM_LABEL = {'v12_long_only': 'v1.2 只做多', 'v11_reversal': 'v1.1 反手'}


def pct(x):
    return '—' if x is None else f'{x*100:+.1f}%'


def bp(x):
    return '—' if x is None else f'{x:+.1f}'


def table(rows, header):
    line = '| ' + ' | '.join(header) + ' |'
    rule = '|' + '|'.join(['---'] * len(header)) + '|'
    return '\n'.join([line, rule] + ['| ' + ' | '.join(r) + ' |' for r in rows])


def main():
    data = json.loads((EXP / 'results/summary.json').read_text())
    blocks = []
    overview, controls = [], []
    for w in data['windows']:
        hold = w['buy_and_hold']
        for arm in w['arms']:
            m = arm['metrics']
            overview.append([w['symbol'].split('_')[0], w['window'], ARM_LABEL[arm['arm']],
                             str(m['closed']), f"{m['net_win_rate']*100:.1f}%" if m['net_win_rate'] is not None else '—',
                             bp(m['mean_net_bp']), f"{m['profit_factor']:.2f}" if m['profit_factor'] else '—',
                             pct(arm['compounded_return']), f"{arm['time_in_market']*100:.0f}%",
                             pct(hold['net_return'])])
            controls.append([w['symbol'].split('_')[0], w['window'], ARM_LABEL[arm['arm']],
                             str(m['control_matched_targets']), bp(m['matched_target_mean_net_bp']),
                             bp(m['control_mean_net_bp']), bp(m['excess_net_bp']),
                             f"{m['p_value']:.3f}" if m['p_value'] is not None else '—',
                             str(m['block_count'])])
    blocks.append('### 主表：每臂 vs 买入持有\n\n' + table(overview,
        ['币', '窗口', '臂', '笔数', '净胜率', '均净bp', 'PF', '复利净收益', '持仓占比', '买入持有净']))
    blocks.append('### 对照表：匹配随机入场\n\n' + table(controls,
        ['币', '窗口', '臂', '配对数', '本策略均净bp', '随机均净bp', '超额bp', '置换p', '周块数']))
    risk = [[w['symbol'].split('_')[0], w['window'], ARM_LABEL[a['arm']],
             bp(a['metrics']['avg_win_bp']), bp(a['metrics']['avg_loss_bp']),
             bp(a['metrics']['max_loss_bp']), str(a['metrics']['max_consecutive_losses']),
             bp(a['metrics']['worst_open_mae_bp'])]
            for w in data['windows'] for a in w['arms']]
    blocks.append('### 风险剖面\n\n' + table(risk,
        ['币', '窗口', '臂', '均盈bp', '均亏bp', '最大单笔亏bp', '最长连亏', '最差持仓MAE bp']))
    signals = [[w['symbol'].split('_')[0], w['window'], f"{w['start'][:10]} → {w['end'][:10]}",
                str(w['evaluated_bars']), str(w['entry_signals']), str(w['flat_signals']),
                str(w['arms'][0]['metrics']['closed'] + w['arms'][0]['metrics']['open'])]
               for w in data['windows']]
    blocks.append('### 信号统计\n\n' + table(signals,
        ['币', '窗口', '区间(UTC)', '评估bar', '入场信号', '平仓信号', 'v1.2 持仓次数']))
    print('\n\n'.join(blocks))


if __name__ == '__main__':
    main()
