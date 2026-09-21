"""Render already-computed conditional oracle labels; never optimize or fetch data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd

EXP = Path('experiments/active/exp-winner-roll-max10r-20260921-v1')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render(run: Path):
    summary = json.loads((run/'summary.json').read_text())
    for name, sha in summary['files'].items():
        if digest(run/name) != sha:
            raise ValueError(f'Changed result: {name}')
    t = pd.read_csv(run/'trades.csv')
    a = pd.read_csv(run/'per_asset.csv')
    legs = pd.read_csv(run/'legs.csv')
    two = a[a.arm.eq('two')].copy()
    balances = t.pivot(index='event_key', columns='arm', values='final_balance').add_prefix('balance_')
    two = two.merge(balances, on='event_key', validate='one_to_one').sort_values('balance_two', ascending=False)
    two['entry_beijing'] = pd.to_datetime(two.original_entry_time, utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    two['exit_bar_beijing'] = pd.to_datetime(two.optimizer_exit_bar_open, utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    two['net_profit'] = two.balance_two-100
    two['capital_multiple'] = two.balance_two/100
    names = {'asset':'币种', 'venue':'交易所', 'timeframe_min':'周期_分钟', 'entry_beijing':'入场时间_北京',
             'original_net_r':'原交易净R', 'balance_none':'不加仓最高资金U', 'balance_one':'一次加仓最高资金U',
             'balance_two':'两次加仓最高资金U', 'net_profit':'两次加仓净利润U', 'capital_multiple':'本金倍数',
             'adds_count':'实际加仓次数', 'exit_bar_beijing':'退出所在K线_北京', 'optimizer_exit_price':'退出价格',
             'min_maintenance_buffer':'最低维持保证金余量U', 'selected_trades':'该币入选笔数',
             'failed_trades':'失败笔数', 'real_exchange_solvency':'真实交易所不爆仓状态', 'event_key':'事件ID'}
    two[list(names)].rename(columns=names).to_csv(EXP/'逐币最高资金.csv', index=False, encoding='utf-8-sig')
    wif = t[t.asset.eq('WIF') & t.venue.eq('okx') & t.timeframe_min.eq(60) &
            pd.to_datetime(t.original_entry_time, utc=True).eq(pd.Timestamp('2026-08-19T18:00Z'))]
    if len(wif) != 3:
        raise ValueError('Expected one exact screenshot WIF event and three arms')
    w = wif.set_index('arm')
    event = str(wif.event_key.iloc[0])
    wl = legs[legs.event_key.eq(event) & legs.arm.eq('two')].copy()
    wl['time_beijing'] = pd.to_datetime(wl.time_utc,utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    wl['total_quantity'] = wl.quantity.cumsum()
    wl['total_notional_at_fill'] = wl.total_quantity*wl.price
    wl.to_csv(EXP/'WIF截图单_最优两次加仓.csv',index=False,encoding='utf-8-sig')
    lines = [
        '# 大于10R多单：100U最多两次加仓的条件历史最大值', '',
        f"完成{summary['scope']['successful_trades']}/367笔、209资产、339流；失败{summary['scope']['failed_trades']}笔。每笔独立100U。",
        '**以下金额是已知未来行情后的条件最优标签，不是可执行策略收益，也不是已验证的真实交易所不爆仓金额。**', '',
        f"WIF截图同单（OKX、1H、北京时间2026-08-20 02:00入场）：不加仓最高{w.loc['none','final_balance']:,.2f}U，一次{w.loc['one','final_balance']:,.2f}U，两次{w.loc['two','final_balance']:,.2f}U。", '',
        '## 固定口径', '',
        '- 只选旧V9原始净R严格大于10、已闭合多单；原始R不是账户收益率。入场跨度2024-09-10至2026-09-09；30m217笔、1H102笔、4H48笔。Binance232、OKX114、Gate21。',
        '- 原入场不变；最多两个后续更高open加仓；穷举原退出bar之前的所有完整bar退出high。所选退出根low也必须存活，未假定high先到。每个0/1/2次方案各自联合优化退出。',
        '- 40倍开仓上限、平坦5%MMR、0.1%清算费预留、0.01U严格正缓冲、开平各0.1%手续费。零资金费、成交价low代理mark、连续数量、无历史风险档位或订单深度上限。',
        '- 初始资金固定100U，浮盈计入权益；加仓支付手续费且重算全仓初始保证金，每根low重算全仓维持保证金。没有额外注入本金，也不把浮盈再当现金加一遍。', '',
        '## 逐币结果（按两次加仓最高资金排序；完整209币见CSV）', '',
        '| 币种 | 交易所/周期 | 原净R | 不加仓最高U | 一次最高U | 两次最高U |',
        '|---|---|---:|---:|---:|---:|']
    for row in two.head(25).itertuples():
        lines.append(f'| {row.asset} | {row.venue}/{row.timeframe_min}m | {row.original_net_r:.2f} | {row.balance_none:,.2f} | {row.balance_one:,.2f} | {row.balance_two:,.2f} |')
    lines += ['', f"209个币中，条件最大值超过3,000U的{int(two.balance_two.gt(3000).sum())}个，超过1,000U的{int(two.balance_two.gt(1000).sum())}个。这是事后选赢家及各币最好一笔的分布，不能当未来成功率。", '',
              '## WIF截图同单明细', '', '| 动作 | 北京时间 | 价格 | 新增WIF数量（连续） | 累计数量 |', '|---|---|---:|---:|---:|']
    for row in wl.itertuples():
        lines.append(f'| {"初仓" if row.leg_no == 0 else "加仓"+str(row.leg_no)} | {row.time_beijing} | {row.price:.4f} | {row.quantity:,.6f} | {row.total_quantity:,.6f} |')
    lines += ['', f"退出：{pd.Timestamp(w.loc['two','optimizer_exit_bar_open']).tz_convert('Asia/Shanghai')}这根1H的high {w.loc['two','optimizer_exit_price']:.4f}，不是确切根内成交时刻。开仓费{w.loc['two','opening_fees']:.6f}U、平仓费{w.loc['two','closing_fee']:.6f}U；净利润{w.loc['two','final_balance']-100:,.6f}U。", '',
        '## 与前版的关系及检验口径', '',
        '| 口径 | 前版V0.4 | 本次 |', '|---|---|---|',
        '| 仓位/时机 | 固定因果结构、保留部分浮盈、最多两次 | 已知未来后的全候选联合最优化，最多两次 |',
        '| WIF数字 | 旧特定保证金账本退出2104.06U、最高浮盈3500.03U | 本次平坦5%假设最高7521.87U |',
        '| 可比性 | 原策略结果，不是上限 | 约束与退出目标不同，不能归因成实盘收益提升 |', '',
        '不加仓/一次/两次表是同一原交易与同一费用/保证金模型的最优值对照。前后时间分段为2025-09-10：早157笔、晚210笔，均为已被挑出的历史赢家，不是盲验证集。',
        'val AUC、置换p、预测top-decile毛/净收益、预测胜率、单特征基线：不适用。没有模型分数或预测验证；盈利标签先验选样，再用未来决定仓位与卖点。匹配随机入场检验也不能使这个oracle成为方向性优势证明。本次只回答条件容量；零假设对照是独立LP与完整小路径穷举，经济参照是同一交易不加仓的条件最优。', '',
        '## 风险与诚实声明', '',
        '- 大额结果首先按异常值核查；账本与固定时序LP的独立核查结果见validation_receipt.json。分档MMR、订单量上限、滑点/深度和真实资金费未建模，尤其不能把几十万/百万U当可成交承诺。',
        '- 最优量通常把维持保证金余量压到约0.01U；真实mark微小偏差、资金费或滑点就可能击穿。真实不爆仓状态全部unknown。连续数量也不是交易所合法lot保证。',
        '- 5%MMR和40倍只是统一研究假设；没有断言各交易所在这些日期提供相同规格。OKX现行公式参考：[Futures margin calculation rules](https://www.okx.com/en-gb/help/futures-margin-calculation-rules)，不是历史档位证据。',
        '- 没有随机切分、训练、promote、Pine替换、账户操作或新raw行情落盘。旧实验与旧报告保留。', '',
        '## 复现与证据', '',
        f"市场builder提交：`{summary['identity']['source_commit']}`，先于run_v1。输入aggregate SHA：`{summary['identity']['inputs']['statistics_files']['trades.csv.gz']}`。339条原cache SHA在运行前后相同。", '',
        '```bash', '.venv/bin/python -m pytest tests/evaluation/test_winner_roll_max10r.py tests/evaluation/test_winner_roll_max10r_study.py -q',
        '.venv/bin/python -m yoyo.evaluation.winner_roll_max10r_study --output experiments/active/exp-winner-roll-max10r-20260921-v1/run_reproduce',
        '.venv/bin/python -m yoyo.evaluation.winner_roll_max10r_report --run experiments/active/exp-winner-roll-max10r-20260921-v1/run_v1', '```', '',
        '复现需要本仓原始冻结数据，不能覆盖已有run目录。结果CSV、逐流完成收据、summary与最终delivery manifest共同保留来源。', '',
        '## 下一步选项', '',
        '如Owner进一步要求实盘可行金额，再按指定币/交易所补历史标记价格、资金费、档位和订单限制重算；不能自动把本次标签改为实盘策略。']
    report = Path('analysis/p1_winner_roll_max10r_20260921.md')
    report.write_text('\n'.join(lines)+'\n')
    receipt = {'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
               'report_builder_sha256':digest(__file__), 'run_summary_sha256':digest(run/'summary.json'),
               'outputs':{str(p):digest(p) for p in [report,EXP/'逐币最高资金.csv',EXP/'WIF截图单_最优两次加仓.csv']}}
    (EXP/'report_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(report),'coins':len(two),'wif_two':float(w.loc['two','final_balance'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=EXP/'run_v1')
    render(parser.parse_args().run)
