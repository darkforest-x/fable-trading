"""Render the frozen net-recovery verification from completed artifacts only."""
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/active/exp-spike-eth3m-net-recovery-20260914-v3'
ARM={'legacy_gross1':'旧毛1R／无保本','next_bar':'净1R／下一根保护',
     'ohlc':'净1R／O-H-L-C即时保护','olhc':'净1R／O-L-H-C即时保护'}
CASH={'fixed10':'固定1U／10倍容量','double10':'翻倍／10倍容量',
      'double_risk_only':'翻倍／理想风险容量'}


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,r))+' |' for r in rows])+'\n'


def results_table(t):
    rows=[]
    for r in t.itertuples():
        rows.append([ARM[r.arm],CASH[r.cash],r.n_natural,
            f'{r.n_profit}/{r.n_net_be}/{r.n_net_loss}',f'{r.final_balance:.2f}',
            f'{r.max_initial_stop_streak}/{r.max_net_loss_streak}',
            f'{r.mean_gross_r:.3f}/{r.mean_net_r:.3f}',
            f'{r.random_mean_net_r:.3f}',f'{r.excess_net_r:+.3f}',f'{r.p:.4f}'])
    return table(['退出','资金','自然笔数','赢／净保本／亏','期末U','完整止损／净亏最长',
                  '均值毛／净R','匹配随机净R','超额R','月块p'],rows)


def main():
    result=EXP/'results';out=result/'evaluation'
    s=pd.read_csv(out/'summary.csv');c=pd.read_csv(out/'controls.csv')
    joined=s.merge(c,on=['period','arm','cash'],validate='one_to_one')
    primary=s[(s.period=='continuous_pre')&(s.cash=='double10')].set_index('arm')
    a=primary.loc['ohlc'];b=primary.loc['olhc'];n=primary.loc['next_bar']
    text=f'''# ETH3m：净1R止盈、费用保本与整轮回本重置验证

2026-09-14｜exp-spike-eth3m-net-recovery-20260914-v3｜冻结后验证，未启用

**费用保本的纠正有效：本轮三个新退出情景的已成交记录中，没有出现9次连续完整初始止损，最高为{int(s[s.arm!='legacy_gross1'].max_initial_stop_streak.max())}次。但这不等于倍投可持续。10倍容量的连续盘中路径账户，完整初始止损最长仅{int(a.max_initial_stop_streak)}/{int(b.max_initial_stop_streak)}次，仍因未回收亏损累积到第9次、下一笔风险512U而无法继续；余额分别{a.final_balance:.2f}U和{b.final_balance:.2f}U。**

这是三分钟OHLC下的固定规则验证。O-H-L-C与O-L-H-C是两种明确路径假设，不是已观察到的盘中先后顺序，也不是严格收益上下界。本轮40个窗口×退出×资金账户均未盈利，不能称已证明实盘必亏，更不能称必赢。未读取新holdout。

## 实际验证的规则

Owner要求“保本的时候提高0.2%”，随后说“验证”。首笔仍按此前价格止损风险1U、本金1000U；V8入场、5bar结构／0.2ATR缓冲／2ATR下限、原V6反向次开、原2R收盘启用4ATR跟踪及20bp名义往返研究成本保持。

1. **净1R全平**：目标毛R=1+cost_R，cost_R=0.002/初始价格风险比例。没有把“毛1R”当净1R。
2. **净费用保本**：多头开仓价×1.002向上取tick，空头×0.998向下取tick。价格再向有利方向走1tick后触发，避免将止损直接放到当前触发价造成立即退出。
3. **亏后翻倍**：每笔扣费净亏后风险×2；净保本和未回本的小盈利保持当前风险。只有本轮累计实际净现金>=0才从1U重新开始。
4. **不清债、不设六层重置**：保证金／费用容量不足拒绝当前信号并等待后续可承受信号，保留债务；价格风险本身已经不低于余额时停止。拒单不占仓位。
5. **三个执行情景全部事前冻结**：下一bar才保护；O-H-L-C逐段路径即时保护；O-L-H-C逐段路径即时保护。没有在看完结果后挑一个作为真实盘口。

例如3000开多、初始止损2990，价格R=10：费用保本价3006，触发3006.01，净1R目标3016。正常在3006退出对应模型净0；实际有利tick带来的微利保留在现金，但统计类型仍为保本。跳空滑过保护价造成的真实净亏仍记亏。

**1U指价格止损预算，未改为“含费用最多亏1U”。**成本按名义仓位计算，窄止损会提高cost_R，因此一次完整止损净亏可能大于该笔价格风险金额。净1R赢单也未必覆盖机械翻倍之前的全部含费亏损；只有实际周期账本确认回本才复位。

真实交易费用依成交数量、价格和费率；固定20bp只是本研究假设。[OKX费用说明](https://www.okx.com/en-gb/help/how-to-calculate-the-contract-transaction-fee) 触发价不保证成交价，盘口滑点和触发价格类型仍需另验。[OKX止损说明](https://www.okx.com/en-gb/help/how-do-i-modify-take-profit-tp-and-stop-loss-sl)

## 数据、冻结与对照

仅已有OKX ETH-USDT-SWAP三分钟上下文，482176bar，2023-07-31 11:12至2026-04-30 23:57 UTC。开发2023-08至2024-12、验证2025年、2026年前4月各独立1000U，连续账户2023-08至2026-04只投入一次1000U。开发机会923条、2025年653条、2026前4月233条；不是所有机会都能在单仓和资金容量下成交。已使用过的历史不是全新盲测。**这是该配置第0次消耗holdout。**

builder/config/计划在f0a4847ac2提交后才运行；没有开发筛选阶段，没有根据结果改参数。旧毛1R不保本固定1U复现开发809笔、437.586101U，是旧口径参考而非原V8无固定TP退出。

同退出固定1U用于区分退出与资金倍率。同ETH／UTC月／方向／当前ATR比例相对此前120bar分位桶匹配9个随机入场，使用相同退出和成本；9999次月份区块符号置换，种子20260914。控制仅匹配自然平仓子样本并保留重抽次数和控制索引，不是可执行随机资金账户。净保本不会被记为亏，也不把有利tick的几分钱包装成一次目标盈利。
'''
    for period,title in [('development','开发期：2023-08至2024-12'),('validation','2025年验证'),('preholdout','2026年前4月验证'),('continuous_pre','连续账户：2023-08至2026-04')]:
        text+='\n## '+title+'\n\n'
        text+=results_table(joined[joined.period==period])
    text+='\n## 连续账户的容量与覆盖\n\n没有停止标记不代表一直能开仓。期末余款不是强平余额；没有收到新资金。以下只列加码组。\n\n'
    rows=[]
    for r in s[(s.period=='continuous_pre')&(s.cash!='fixed10')].itertuples():
        ledger=pd.read_csv(out/(r.period+'_'+r.arm+'_'+r.cash+'_ledger.csv.gz'))
        natural=ledger[ledger.accepted]
        rows.append([ARM[r.arm],CASH[r.cash],r.n_capacity_rejected,str(natural.entry_time.iloc[-1])[:16],
            f'{r.max_unrecovered_loss_events}',f'{r.max_risk:.0f}/{r.max_attempted_risk:.0f}',
            f'{r.residual_debt:.2f}',f'{100*r.max_realized_drawdown:.2f}%',
            str(r.halt_time)[:16] if pd.notna(r.halt_time) else '未永久停机／保留债务等待'])
    text+=table(['退出','资金','容量拒单','最后实际开仓UTC','未回本累计净亏事件','最大已用／尝试风险U','残留债务U','现金回撤','停止UTC'],rows)
    text+=f'''
下一bar保护＋10倍容量组虽然留有{n.final_balance:.2f}U，但整个连续区间只成交{int(n.n_natural)}笔，容量拒单{int(n.n_capacity_rejected)}次，最后实际开仓在2025-03-04。此后的长期等待不能冒充策略持续盈利或资金安全。

## 直接核对：只有短连续止损仍可累积到大风险

下面是连续O-H-L-C、10倍容量账户实际采纳的研究成交；省略净保本的中间行时不改变债务。完整循环及每个拒单都保存在对应ledger。下表包括该成交的匹配随机净R作诊断。
'''
    tag='continuous_pre_ohlc_double10'
    ledger=pd.read_csv(out/(tag+'_ledger.csv.gz'));matched=pd.read_csv(out/(tag+'_matched.csv.gz')).set_index('signal_i')
    accepted=ledger[ledger.accepted]
    chosen=accepted[(accepted.reason=='take_profit')|(accepted.entry_time.str.startswith('2025-03-04'))|
                    (accepted.entry_time.str.startswith('2023-12-06'))]
    text+=table(['入场UTC','退出','本笔价格风险U','净盈亏U','本轮此前净债务U','本轮剩余净债务U','匹配随机净R'],
        [[str(r.entry_time)[:16],r.reason,f'{r.risk:.0f}',f'{r.pnl:+.4f}',f'{max(0,-r.cycle_net_before):.4f}',
          f'{max(0,-r.cycle_net_after):.4f}',f'{matched.loc[r.signal_i,"random_net_r"]:.3f}'] for r in chosen.itertuples()])
    text+='''
2023-11-21这一笔已经兑现净1R：64U风险赚64.0024U，但此前净债务115.2252U，赢后仍欠51.2228U。随后保本只产生tick级微利，没有清除债务。2025-03-04的256U价格风险净亏267.2327U后，账户463.2514U，下一笔需要承担512U价格风险；2025-03-05拒绝继续。本轮累计9个净亏事件，中间夹着保本和一笔盈利，最长连续完整初始止损仅2次。

再去掉10倍保证金约束，只保留止损风险和费用容量：开发O-H-L-C/O-L-H-C诊断一轮最长连续完整止损仅3，期间4笔净保本，8个未回本净亏事件后剩101.233041U；下一笔256U无法承担。这说明失败不要求先出现9次连续完整止损。该理想容量诊断不代表真实可实现杠杆。

## 归因、风险与诚实声明

费用保本改变了真实分类：正常保本没有计亏；延迟到下一bar才生效时，可能已回落到保护价另一侧，必须按下一开盘实际价格计亏，不伪造保本成交。盘中两条路径减少这类损失，但目标盈利也很容易被早期保本截断。只看“没亏的比例”会把大量净0误当盈利。

连续止损与未回收亏损事件不同；机械翻倍取决于后者。费用保本解决本笔费用，不解决前债，也不把含费止损亏损压回价格1R。较高账户余额可能来自大量拒单、长时间无法加码，不能用它证明更好交易收益。跨窗口入场准入不同、独立本金与连续本金不同，不能把各段期末直接相加。

OHLC仅有四价，不证明瞬时触发、改单延迟、队列或真实路径。两种路径结果一致失败不是对所有可行bar内路径的数学证明。固定研究成本未替换为Owner实际费率；没有模拟资金费、标记价强平、维持保证金分档、最小张数或真实滑点。现金回撤按整笔退出/边界估计清算权益，不是持仓内峰谷。边界标记不算自然胜负，也不作为回本复位。

无训练、无特征选择、无事前排序分数，AUC与top-decile收益不适用，不事后挑盈利前10%。单变量对照是同退出固定风险、同净1R/费用保本只改生效时序；规则整体相对旧毛1R属于Owner授权联合语义修改，不把收益差全归因于0.2%止损位置。匹配随机与月份置换替代无关分类指标。少量加码成交、少数月份的p不构成实盘确认；即使有正超额R，绝对净收益仍可能为负。

## 验证与复现

132项退出/资金/层边界/holdout边界定向测试通过。开发3个新退出共2769机会行独立用side×(exit-entry)/initial_risk−cost_R重算，最大误差3.05e-13；正常保本均不低于−1e-9，TP最低净R大于1。10个开发账本6168行现金独立核对，最大误差1.88e-12。全部40账户运行时现金与周期和守恒断言通过；全库旧失败和本次验证范围在results/validation.json诚实保留，不声称所有门全绿。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --capture=no tests/evaluation/test_spike_net_recovery_exit.py tests/evaluation/test_spike_net_recovery_cash.py
# 首先确认main、提交builder/config/PROJECT_PLAN，输出目录需尚不存在
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_net_recovery_study
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/report_spike_net_recovery.py
```

输出拒绝覆盖，重现需要保留旧证据后的独立输出安排，不静默删除产物。config及frozen.json记录来源SHA、参数与代码提交，receipt/manifest记录40账户和文件哈希；longest_streaks.csv与halt_cycles.csv保留最长段及终止循环，匹配CSV记录每个随机控制入场索引。HTML为正式交付，Markdown为版本源。

## 下一步边界

本冻结规则未通过盈利验证，不启用、不promote。可保留“净费用保本”“实际净现金回本才复位”的明确语义；若改为含费净止损预算1U，或改为累计净债务决定下笔风险，属于新的资金规则，不能沿用本轮结果。盘中即时效果要靠更细粒度历史/前向成交验证；新holdout使用与真实交易仍需Owner授权。不存在由本轮历史统计推出未来必赢的依据。
'''
    report=ROOT/'analysis/p1_spike_eth3m_net_recovery_20260914.md'
    report.write_text(text)
    subprocess.run(['.venv/bin/python','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)


if __name__=='__main__':main()
