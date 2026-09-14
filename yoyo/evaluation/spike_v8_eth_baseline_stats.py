"""Describe the already frozen V8 ETH trade ledger without a strategy replay.

Source: SHA-pinned baseline_event_ledger from the 2026-09-14 cost diagnostic.
No OHLCV or new features are read. MFE is the legacy engine's conservative
pre-exit-bar observation, not a fixed-target fill. Streaks break at fold
boundaries because those folds were independently replayed from flat.
"""
from pathlib import Path
import hashlib
import json
import subprocess

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-v8-eth-baseline-stats-20260914-v1'
SOURCE = ROOT / 'experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/baseline_event_ledger.csv.gz'
SOURCE_SHA = 'b14ff66a312f6b9d30fd86ddbfb90cd90c68784efe4cf690de770f4e0063c673'
REPORT = ROOT / 'analysis/p1_spike_v8_eth_baseline_stats_20260914.md'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runs(values):
    """Return inclusive start/end indices for every uninterrupted True run."""
    found, start = [], None
    for i, value in enumerate(list(values) + [False]):
        if value and start is None:
            start = i
        elif not value and start is not None:
            found.append((start, i - 1))
            start = None
    return found


def masks(frame):
    stop = frame.exit_reason.str.contains('stop', regex=False)
    return {
        'net_loss': frame.net_r.lt(0),
        'gross_loss': frame.gross_r.lt(0),
        'net_loss_stop': stop & frame.net_r.lt(0),
        'gross_loss_stop': stop & frame.gross_r.lt(0),
        'initial_stop': frame.exit_reason.str.startswith('initial_stop'),
        'net_win': frame.net_r.gt(0),
    }


def describe(frame):
    n = len(frame)
    gains, losses = frame.net_r.clip(lower=0).sum(), -frame.net_r.clip(upper=0).sum()
    row = dict(n=n, net_wins=int(frame.net_r.gt(0).sum()), gross_wins=int(frame.gross_r.gt(0).sum()),
               net_win_rate=float(frame.net_r.gt(0).mean()), gross_win_rate=float(frame.gross_r.gt(0).mean()),
               net_flat=int(frame.net_r.eq(0).sum()), sum_net_r=float(frame.net_r.sum()),
               mean_net_r=float(frame.net_r.mean()), median_net_r=float(frame.net_r.median()),
               pf_net_r=float(gains / losses) if losses else None,
               best_net_r=float(frame.net_r.max()), worst_net_r=float(frame.net_r.min()),
               first_entry=str(frame.entry_time.min()), last_exit_bar=str(frame.exit_time.max()))
    for threshold in [1, 2, 3, 5, 10]:
        for column in ['mfe_r', 'gross_r', 'net_r']:
            count = int(frame[column].ge(threshold - 1e-10).sum())
            row[f'{column}_ge{threshold}_n'] = count
            row[f'{column}_ge{threshold}_rate'] = count / n
    for key, value in masks(frame).items():
        row[f'{key}_n'] = int(value.sum())
    return row


def table(frame):
    return frame.to_markdown(index=False, floatfmt='.4f')


def main():
    if sha(SOURCE) != SOURCE_SHA:
        raise ValueError('source ledger hash changed')
    out = EXP / 'results'
    out.mkdir(exist_ok=False)
    df = pd.read_csv(SOURCE)
    assert len(df) == 3525 and not df.censored.any() and df.arm.eq('v8').all()
    assert not df.duplicated(['stream', 'period', 'entry_time']).any()
    for col in ['entry_time', 'exit_time']:
        df[col] = pd.to_datetime(df[col], utc=True)
    assert ((df.gross_return - df.net_return - 0.002).abs() < 1e-10).all()
    summaries, streaks, exits = [], [], []
    for (stream, period), group in df.groupby(['stream', 'period']):
        group = group.sort_values('entry_time').reset_index(drop=True)
        row = dict(stream=stream, period=period, **describe(group))
        for kind, value in masks(group).items():
            spans = runs(value.tolist())
            row['max_consecutive_' + kind] = max([b-a+1 for a,b in spans], default=0)
            for a,b in spans:
                part = group.iloc[a:b+1]
                streaks.append(dict(stream=stream, period=period, kind=kind, length=b-a+1,
                                    first_entry=part.iloc[0].entry_time.isoformat(),
                                    last_exit_bar=part.iloc[-1].exit_time.isoformat(),
                                    sum_net_r=float(part.net_r.sum()),
                                    first_entry_beijing=part.iloc[0].entry_time.tz_convert('Asia/Shanghai').isoformat(),
                                    last_exit_bar_beijing=part.iloc[-1].exit_time.tz_convert('Asia/Shanghai').isoformat()))
        summaries.append(row)
    details = pd.DataFrame(summaries)
    for stream, group in df.groupby('stream'):
        row = dict(stream=stream, period='all_observed_folds', **describe(group))
        for key in masks(group):
            row['max_consecutive_' + key] = int(details.loc[details.stream.eq(stream), 'max_consecutive_' + key].max())
        summaries.append(row)
        for reason, part in group.groupby('exit_reason'):
            exits.append(dict(stream=stream, exit_reason=reason, n=len(part), wins=int(part.net_r.gt(0).sum()),
                              sum_net_r=float(part.net_r.sum())))
    summary, streak, exit_frame = pd.DataFrame(summaries), pd.DataFrame(streaks), pd.DataFrame(exits)
    summary.to_csv(out/'summary.csv', index=False)
    streak.sort_values(['stream', 'kind', 'length'], ascending=[True,True,False]).to_csv(out/'streaks.csv', index=False)
    exit_frame.to_csv(out/'exit_reasons.csv', index=False)
    all_rows = summary.loc[summary.period.eq('all_observed_folds')]
    rows = []
    for row in all_rows.itertuples():
        rows.append([row.stream, row.n, f'{row.net_wins} / {row.net_win_rate:.2%}', f'{row.gross_wins} / {row.gross_win_rate:.2%}',
                     row.max_consecutive_net_loss, row.max_consecutive_net_loss_stop,
                     row.max_consecutive_gross_loss_stop, row.max_consecutive_initial_stop,
                     f'{row.mfe_r_ge3_n} / {row.mfe_r_ge3_rate:.2%}',
                     f'{row.net_r_ge3_n} / {row.net_r_ge3_rate:.2%}'])
    main_table = pd.DataFrame(rows, columns=['流','平仓笔数','净赢笔数/胜率','毛赢笔数/胜率','最长连续净亏','最长连续净亏止损','最长连续价格亏损止损','最长连续初始止损','记录浮盈≥3R','最终净赚≥3R'])
    lines = ['# V8 ETH 永续 3分钟 / 5分钟：原始交易统计', '',
             '这里回答信号与交易本身，不施加1000U资金、倍投、保证金或拒单筛选。数据来自既有冻结V8自然平仓账本；没有新回放或改变止盈止损。', '',
             '## 核心结果', '', table(main_table), '',
             '3m：OKX ETH-USDT-SWAP，原始数据2023-07-31至2026-07-30；5m：Binance ETHUSDT永续，2020-01-01至2026-05-01。两者交易所和区间不同。统计为全部已保存自然平仓，不包括窗口末尚未平仓。历史分段从空仓重启，连续次数在分段边界归零，不把两段拼成未经回放的连续序列。', '',
             '## 定义', '',
             '- 1R为实际下一根开盘入场价与初始止损价的距离；净结果扣每笔名义本金0.2%往返成本。价格上涨获利但不够覆盖成本的单，会成为净亏。',
             '- 连续净亏：逐笔net_R<0，任何其他结果中断。连续净亏止损：每笔既是stop退出又net_R<0；反向退出无论盈亏都中断这项连续记录。连续初始止损只数initial_stop及跳空版本。盈利跟踪止损不算亏损止损。',
             '- V8无固定3R止盈。记录浮盈≥3R是原引擎MFE列的保守统计：引擎先判断退出，发生退出的那根不更新MFE；它不是完整逐tick最高浮盈，也不是固定3R策略胜率。最终净赚≥3R是实际退出扣费后的结果，两者不能混用。', '',
             '## V8冻结规则', '',
             'V6均线密集启动确认 + V7此前BB200压缩连续3根门 + V8同方向绳索距离≤3ATR；六线为SMA/EMA 20、60、120。确认后次根开盘入场，同流单仓，可多可空。初始保护取含信号根最近5根极值外0.2ATR与信号收盘外2ATR中较远者；按tick向外取整。收盘达到2R后启用4ATR跟踪，收盘更新、次根有效；原V6反向信号次根开盘退出。无固定3R止盈、无分批止盈。', '',
             '## 分段统计', '', table(details[['stream','period','n','net_wins','net_win_rate','max_consecutive_net_loss','max_consecutive_net_loss_stop','mfe_r_ge3_n','net_r_ge3_n','mean_net_r','pf_net_r']]), '',
             '## R分布', '']
    rr=[]
    for row in all_rows.to_dict('records'):
        for k in [1,2,3,5,10]:
            rr.append([row['stream'], k, row[f'mfe_r_ge{k}_n'], row[f'gross_r_ge{k}_n'], row[f'net_r_ge{k}_n']])
    lines += [table(pd.DataFrame(rr,columns=['流','R门槛','记录浮盈达到','实际毛利润达到','实际净利润达到'])), '',
              '## 最长亏损段的时间', '']
    tops = streak.loc[streak.kind.isin(['net_loss','net_loss_stop','initial_stop'])].sort_values('length',ascending=False).groupby(['stream','kind'],sort=False).head(1)
    lines += [table(tops[['stream','kind','length','first_entry_beijing','last_exit_bar_beijing','sum_net_r']]), '',
              '时间为北京时间；退出时刻是退出K线开盘标记，盘中实际退出可能晚3/5分钟。完整连续段在streaks.csv，不能把历史最长次数当未来上限。', '',
              '## 退出原因', '', table(exit_frame), '',
              '## 收益描述与边界', '',
              table(all_rows[['stream','sum_net_r','mean_net_r','median_net_r','pf_net_r','best_net_r','worst_net_r']]), '',
              '累计R仅为逐笔等风险单位相加，不是1000U账户收益。本轮为固定账本的描述性汇总，无预测器、无排序选择、无新入场比较，AUC/top-decile/新置换检验不适用；不把胜率或连亏统计解释为alpha。原研究后段匹配随机对照与检验见原低周期报告，不将其p值借作3R规则的证据。', '',
              '既有原研究后段3m相对匹配随机净R差+0.4091、p=0.2559；5m差+0.1931、p=0.06，均未达到项目p<0.01门槛。这些是原研究既有对照，不是本轮新检验，也不能代表全时段。', '',
              '## 数据、复现与诚实声明', '',
              f'输入：`{SOURCE.relative_to(ROOT)}`，SHA256 `{SOURCE_SHA}`。原自然平仓3525笔，3m1571笔、5m1954笔。只重用已授权且已暴露的同一V8账本，不读取OHLCV，不评估新策略；本描述配置第1次重用既有结果，不是新增独立holdout验证。原低周期主回放记录为正式holdout曝光第3次，旧记录不重置。', '',
              '三分钟数据无法消除同根先止盈还是先止损的歧义；固定3R退出还会释放原持仓期间的新机会，不能拿MFE截断旧单代替完整新策略回放。当前Python冻结参数也不自动代表用户任意TradingView图表设置。', '',
              '```bash', '.venv/bin/python -m pytest -q tests/evaluation/test_spike_v8_eth_baseline_stats.py',
              '.venv/bin/python -m yoyo.evaluation.spike_v8_eth_baseline_stats',
              '```', '', '已有results目录拒绝覆盖；重新归约需另立输出版本。无订单或生产配置变更。', '',
              '来源：[原V8低周期报告](p1_spike_v8_lowtf_20260913.md)；[完整规则与成本诊断](p1_spike_eth_lowtf_cost_diagnostic_20260914.md)。', '',
              '下一步由3R口径决定：原始结果描述已完成；固定3R止盈属于另一套退出规则，需单独回放后才能给出其胜率和连续止损。']
    REPORT.write_text('\n'.join(lines)+'\n')
    subprocess.run([str(ROOT/'.venv/bin/python'), str(ROOT/'scripts/md_to_html.py'), str(REPORT), '--out-dir', str(ROOT/'analysis/html')], check=True)
    manifest = dict(source_sha256=SOURCE_SHA, rows=len(df), no_ohlcv_read=True, no_policy_change=True,
                    code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                    outputs={str(p.relative_to(ROOT)):sha(p) for p in list(out.glob('*.csv')) + [REPORT, ROOT/'analysis/html'/REPORT.with_suffix('.html').name]})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(main_table.to_string(index=False))


if __name__ == '__main__':
    main()
