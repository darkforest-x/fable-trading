"""Render frozen grid artifacts without opening price files or reselecting arms."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.bb_stoch_optimization import EXP, ROOT, identity
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec
from yoyo.evaluation.eth_bb_stoch_study import save

REPORT = ROOT/'analysis/p1_eth_bb_stoch_optimization_20260916.md'


def get(name):
    return json.loads((EXP/name).read_text())


def fmt(value, digits=2):
    return '—' if value is None else f'{value:,.{digits}f}'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |',
                      *['| '+' | '.join(map(str,row))+' |' for row in rows]])


def parameter_text(p):
    return f"BB {p['bb_length']} / {p['bb_mult']:g}；止损 {p['stop_fraction']*100:g}%"


def compact_parameters(p):
    return f"{p['bb_length']} / {p['bb_mult']:g} / {p['stop_fraction']*100:g}%"


def performance_row(label, arm, mode):
    v = arm['modes'][mode]; s,e,c = v['stats'],v['equity'],v['control']
    return [label, {'tv':'TV','adverse_first':'逆向优先'}[mode], f"{s['natural']} / {s['censored']}", fmt(e['net_liquidation_mark_usdt']),
            fmt(e['close_mtm_drawdown_usdt']), fmt(s['cash_profit_factor']),
            fmt(s['win_rate']*100 if s['win_rate'] is not None else None,1),
            fmt(c['random_mean_bp']), fmt(c['excess_mean_bp']), c['paired_n'], fmt(c['p'])]


def main():
    cfg,sel = get('config.json'),get('selection.json')
    dev,later = get('development_summary.json'),get('recheck_summary.json')
    sources = {s:get(f'{s}_source_receipt.json') for s in ['development','recheck']}
    receipts = {s:get(f'{s}_code_receipt.json') for s in ['development','recheck']}
    validation = {s:get(f'{s}_validation.json') for s in ['development','recheck']}
    baseline = identity(ParamSpec())
    labels = {baseline:'原始参数'}
    if sel['peak']: labels[sel['peak']] = labels.get(sel['peak'],'')+' / 前段峰值'
    if sel['primary']: labels[sel['primary']] = labels.get(sel['primary'],'')+' / 邻近参数优选'
    labels = {k:v.strip(' /') for k,v in labels.items()}
    selected = sel['primary']
    diagnosis = ''
    if selected:
        score = min(v['equity']['net_liquidation_mark_usdt'] for v in later[selected]['modes'].values())
        verdict = ('后段复查仍为正收益，但样本与成交精度尚不足以确认实盘优势。' if score>0 else
                   '后段复查未能保持正收益；本轮没有找到可确认有效的参数。')
        lead = f"前段按预定规则选出 **{parameter_text(dev[selected]['params'])}**。{verdict}"
        early=dev[selected]['modes']['tv']; late=later[selected]['modes']['tv']
        diagnosis = (f"候选前段自然平仓净盈亏 **{fmt(early['stats']['net_pnl'])} USDT**，"
                     f"计入期末持仓及预留退出费后为 **{fmt(early['equity']['net_liquidation_mark_usdt'])} USDT**。"
                     f"后段 {late['stats']['natural']} 笔完整交易仅 {late['stats']['wins']} 笔盈利，"
                     f"最长连续亏损 **{late['stats']['max_loss_streak']} 笔**；已平仓净盈亏 "
                     f"{fmt(late['stats']['net_pnl'])} USDT，计入期末持仓后仍亏 "
                     f"{fmt(-late['equity']['net_liquidation_mark_usdt'])} USDT。前段排名提升没有转化为后段盈利。")
    else:
        lead = '没有参数同时达到预先规定的交易数量要求，本轮不选出优胜配置。'
    headers = ['配置','路径','平仓/未平','净盈亏','回撤','PF','胜率%','随机bp','超额bp','配对','p']
    all_rows=[]
    for stage,results in [('1—2 月选参',dev),('3—4 月复查',later)]:
        for key in sel['finalists']:
            for mode in cfg['path_modes']:
                all_rows.append(performance_row(stage[:4]+' · '+('原始' if key==baseline else '候选'),results[key],mode))
    param_rows = [[labels[k],k,parameter_text(dev[k]['params']),
                   '5 / 3 / 3；20 / 80',fmt(dev[k].get('neighborhood_median_usdt'))] for k in sel['finalists']]
    ranked = sorted((k for k in dev if dev[k]['eligible']),key=lambda k:(-dev[k]['worst_net_mark'],dev[k]['worst_close_mtm_drawdown'],k))[:10]
    top_rows=[]
    for k in ranked:
        a=dev[k]; mode=min(a['modes'],key=lambda m:a['modes'][m]['equity']['net_liquidation_mark_usdt'])
        top_rows.append(performance_row(compact_parameters(a['params']),a,mode))
    anchor_rows=[]
    base=dev[baseline]['params']
    for k,a in dev.items():
        if sum(a['params'][field]!=base[field] for field in ['bb_length','bb_mult','stop_fraction'])<=1:
            mode=min(a['modes'],key=lambda m:a['modes'][m]['equity']['net_liquidation_mark_usdt'])
            anchor_rows.append(performance_row(compact_parameters(a['params']),a,mode))
    curve_name='eth_bb_stoch_optimization_20260916_equity.png'
    fig,axes=plt.subplots(2,1,figsize=(12,8),layout='constrained')
    for ax,stage,title in zip(axes,['development','recheck'],['Jan-Feb: parameter selection','Mar-Apr: frozen chronological recheck']):
        curves=np.load(EXP/f'{stage}_selected_curves.npz',allow_pickle=False)
        times=pd.to_datetime(curves['timestamps_ns'],utc=True)
        times=times+pd.Timedelta(minutes=5)
        times=times.insert(0,times[0]-pd.Timedelta(minutes=5))
        for key in sel['finalists']:
            ax.plot(times,curves[key+'__tv']-cfg['initial_equity'],label=key,linewidth=1.3)
            if not np.array_equal(curves[key+'__tv'],curves[key+'__adverse_first']):
                ax.plot(times,curves[key+'__adverse_first']-cfg['initial_equity'],linestyle=':',alpha=.7,label=key+' adverse')
        ax.axhline(0,color='#777',linewidth=.7);ax.set_title(title);ax.set_ylabel('Net liquidation-mark P&L (USDT)')
        ax.grid(alpha=.18);ax.legend(fontsize=8)
    fig.savefig(ROOT/'analysis/html'/curve_name,dpi=150);plt.close(fig)
    presets=dict(experiment_id=cfg['experiment_id'],primary=sel['primary'],peak=sel['peak'],
                 parameters=sel['parameters'],stoch_unchanged=True,production_eligible=False,
                 note='Frozen from development only; original Pine defaults were not overwritten.')
    save(EXP/'parameter_presets.json',presets)
    count_rows=[]
    for stage,label in [('development','1—2 月'),('recheck','3—4 月')]:
        for k in sel['finalists']:
            a=(dev if stage=='development' else later)[k]; c=a['modes']['tv']['control'];s=a['modes']['tv']['stats']
            count_rows.append([label,labels[k],a['signal_count'],s['total_entries'],c['drawn'],c['censored'],c['unpaired_n'],
                               s['marketable_be_approximations'],s['same_open_be_approximations']])
    exp_link='../'+str(EXP.relative_to(ROOT))
    text=f'''# ETH 5min · BB × Stoch 参数搜索

{lead}

{diagnosis}

本轮搜索 **{len(dev)} 组**，符合交易数门槛 **{sel['eligible_count']} 组**。这是固定网格内的历史选择，**不是全局最优，也不是全新样本外验证**。3—4 月已在上一轮基准回测中看过；本轮未使用 5 月之后的保留数据。

## 参数结果

{table(['角色','配置编号','BB / 止损','Stoch / 阈值','前段邻域中位 USDT'],param_rows)}

“邻近参数优选”按本组及三个参数轴各向前后一步的合格邻居收益中位数选出，至少 3 组合格；“前段峰值”按本组前段收益最高选出。两者均在读取本轮后段价格前冻结；后段不重新挑冠军。

## 同期结果与匹配随机对照

全部固定每次 **1 ETH**，每边手续费 **0.1%**。净值盈亏包含期末持仓按最后收盘计价，并预留剩余仓位退出费；PF、胜率只统计自然平仓。收盘回撤包含持仓浮盈浮亏，**不是盘中最大回撤**。初始权益 100,000 USDT 只是曲线记账基数，不表示全仓或杠杆设置。

下表净盈亏、回撤单位均为 USDT；“平仓/未平”分别为自然结束笔数和截止时未平仓笔数。后面参数表的“配置”依次为 BB 长度 / 倍数 / 止损百分比。

{table(headers,all_rows)}

随机对照按相同 ETH / 方向 / UTC 信号月份 / 因果波动桶抽样，每笔 5 次且组内不重复，用相同退出和成本规则；它是事件对照，不能把重叠随机交易的盈亏相加当作可执行组合。bp 是相对于各自开仓名义金额的万分比。相对随机 = 完整配对实际单笔均值 − 对照单笔均值。只保留双方自然结束的完整组用于 p；截尾不重抽。每段仅两个月块，单侧符号翻转检验最小 p=0.25，不能通过项目 p<0.01 的优势门槛。

![按成交现金流与收盘浮动盈亏绘制的净值曲线]({curve_name})

图中两段各自空仓起步，前史只预热；实线为 TV 的固定 OHLC 顺序，虚线（如有）为先走不利方向的敏感性路径。收益排序使用两条路径中较低的净值盈亏。两条路径不是逐笔成交，也不能代表真实盘中顺序的全部可能性。

## 前段收益排名前十

仅展示达到事先规定门槛的配置；取各自收益较低的路径。这里只解释搜索结果，不据后段结果更改选择。

{table(headers,top_rows)}

## 原始参数的单轴对照

以下每组只改 BB 长度、BB 倍数或止损中的一项，其余保留 200 / 2 / 3%。用于观察变化方向；完整网格见交付文件。

{table(headers,anchor_rows)}

## 数据与检查

{table(['阶段','配置','信号数','入场数','随机样本','随机截尾','非完整配对','市价化 BE 近似','同开盘 BE 近似'],count_rows)}

- 选参区间：2026-01-01 00:00 UTC 至 2026-03-01 00:00 UTC；后段：2026-03-01 00:00 UTC 至 2026-05-01 00:00 UTC，均按信号收盘归属，截止点不入场。
- 原始文件仅经时间戳先行检查的前缀读取器访问，前史从 2025-12-20 开始。所有组合使用统一最长 BB 预热门槛。
- BB 长度：{cfg['bb_lengths']}；倍数：{cfg['bb_multiples']}；止损百分比：{[x*100 for x in cfg['stop_fractions']]}。Stoch、50% 平仓比例、成本、入场与反向信号定义未调参。
- 每组至少 20 笔自然平仓，前段每月至少 5 笔。未达门槛的结果保留在完整网格，不参与选优。
- AUC、正类率、top-decile、单特征分类基线不适用：本轮是固定规则参数研究，无监督训练、标签分类或打分排序；以原始配置、单轴变化、匹配随机入场作为对应对照。
- 原版参数的特征及逐笔字典与冻结 v2 回放逐项一致；两条路径的实际与对照成交费用、仓位数量、现金盈亏均逐笔核算，期末曲线与账本一致。

## 风险与诚实声明

- 315 组试验存在多重比较和过拟合风险；邻域选择只能减少孤立尖峰，不能证明稳定有效。
- 当前数据源为 OKX ETH-USDT 永续。不同交易所的 ETHUSDT.P 图表、价格和成交结果可能不同；没有自动替换数据源。
- 5 分钟 OHLC 无法提供真实逐笔顺序。动态 BB 触线价用此前收盘序列推导可成交阈值；没有拿当前最终 BB 回填先前影线。当前仍是本地等价回放，未取得 TradingView 原生策略成交账本的逐单一致性证明。
- BB 可移到亏损一侧。TP50% 命中并不保证盈利；新保本单已被价格越过时按事件价格退出并单列近似，不能宣称都成交在开仓价。
- 无额外滑点、资金费、盘口深度或委托延迟模型；仍未加入 V1 多周期门禁。R 图示保留尾仓距离口径，经济收益按真实 50% 成交权重，选优不用可变止损的 R 分母。
- [上一轮固定参数报告](p1_eth_bb_stoch_backtest_20260916.html)从 12 月开始连续持仓。本轮两段空仓重置，不能把本表累计数与上一轮 −11.02R 直接横比。
- 无模型训练、无实盘部署、无自动 promote；后续保留集评估或实盘准入需要 owner 另行决定。

## 复现与证据

构建器先提交，然后读取选参前缀；选择文件与前段摘要提交后才允许读取后段。选参构建提交：`{receipts['development']['head']}`；后段执行提交：`{receipts['recheck']['head']}`。原始 Pine SHA256：`{cfg['pine_sha256']}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/evaluation/test_bb_stoch_parameter_replay.py tests/evaluation/test_bb_stoch_optimization.py tests/evaluation/test_bb_stoch_replay.py -q
# config、plan、全部 builders 必须与 HEAD 相同；使用 config 指定的本地源文件
.venv/bin/python -m yoyo.evaluation.bb_stoch_optimization development
# 首次研究必须先提交 selection.json 与 development_summary.json，再执行后段
.venv/bin/python -m yoyo.evaluation.bb_stoch_optimization recheck
.venv/bin/python -m yoyo.evaluation.bb_stoch_optimization_report
python3 scripts/md_to_html.py analysis/p1_eth_bb_stoch_optimization_20260916.md --out-dir analysis/html
```

以上是首次从零构建的命令顺序。现有目录为已完成证据，程序拒绝覆盖选择或在后段读价后重跑。若要独立复现，应先保留本轮完整产物和哈希清单，再安排独立研究记录；不能直接覆盖本轮证据。报告生成命令本身只读数值产物，可重复运行。

- [完整 315 组表](../../experiments/active/exp-eth-bb-stoch-optimization-20260916-v1/development_grid.csv)
- [冻结选择](../../experiments/active/exp-eth-bb-stoch-optimization-20260916-v1/selection.json)
- [参数预设](../../experiments/active/exp-eth-bb-stoch-optimization-20260916-v1/parameter_presets.json)
- 官方方法参考：[TradingView 策略文档：成交模拟、前视与过拟合](https://www.tradingview.com/pine-script-docs/concepts/strategies/)。

### 数据读取凭证

```json
{json.dumps(sources,ensure_ascii=False,indent=2)}
```

### 运行检查凭证

```json
{json.dumps(validation,ensure_ascii=False,indent=2)}
```

## 下一步

先在图表上核对所选参数的进出场与 TP50% / BE 行为。如 owner 继续研究，可另行确定新的时间区间或 V1 门禁实验；本轮不根据后段结果追加参数搜索。
'''
    REPORT.write_text(text)
    subprocess.run(['python3','scripts/md_to_html.py',str(REPORT.relative_to(ROOT)),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    print(REPORT.with_suffix('.html').name)


if __name__=='__main__':
    main()
