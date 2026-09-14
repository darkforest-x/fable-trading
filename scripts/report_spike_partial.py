"""Render completed partial-exit comparisons; read only immutable result files."""
import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/active/exp-spike-eth3m-partial-tp-20260914-v1'
NAMES={'whole_3r':'全仓3R／原4ATR跟踪','whole_3r_be1':'全仓3R／1R后移开仓价',
       'p50_1_keep':'1R平50%／残仓旧止损','p50_1_be':'1R平50%／残仓开仓价',
       'p70_1_be':'1R平70%／残仓开仓价','p30_1_be':'1R平30%／残仓开仓价',
       'p50_05_be':'0.5R平50%／残仓开仓价','p50_15_be':'1.5R平50%／残仓开仓价',
       'p50_1_lock05':'1R平50%／残仓锁0.5R','p50_1_netbe':'1R平50%／整笔费用覆盖保护',
       'whole_3r_trail1':'全仓3R／1ATR跟踪','whole_3r_trail2':'全仓3R／2ATR跟踪',
       'whole_3r_trail3':'全仓3R／3ATR跟踪'}


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,r))+' |' for r in rows])+'\n'


def main():
    result=EXP/'results';ev=result/'evaluation'
    sel=json.loads((result/'selection.json').read_text())
    cfg=json.loads((EXP/'config.json').read_text())
    s=pd.read_csv(ev/'summary.csv');c=pd.read_csv(ev/'controls.csv')
    joined=s.merge(c,on=['period','name','cash'],validate='one_to_one')
    chosen=sel['best'];d=s[(s.period=='development')&(s.cash=='fixed')]
    b=d[d.name==chosen].iloc[0];base=d[d.name=='whole_3r'].iloc[0]
    continuous=s[s.period=='continuous_pre']
    continuous_fixed=continuous[continuous.cash=='fixed'].set_index('name')
    escalating=continuous[continuous.cash!='fixed']
    text=f'''# ETHUSDT.P 3分钟：分批止盈和收紧移动止损

2026-09-14｜exp-spike-eth3m-partial-tp-20260914-v1｜仅研究、未启用

**已完成13组退出对照。开发期净权益最高是“{NAMES[chosen]}”，1000U变为{b.final_balance:.2f}U；全仓3R基线为{base.final_balance:.2f}U。开发期盈利组数为{int((d.final_balance>1000).sum())}/13，盈利且完整初始止损与净亏最长均≤6的组数为{sel['hard_pass_count']}。这一结果只能选择诊断候选，不能称为实盘最佳。**

可以做分批，但本轮没有找到能支撑倍投的净盈利方案。2023-08至2026-04连续固定1U，全仓3R期末{continuous_fixed.loc['whole_3r','final_balance']:.2f}U，1R平50%并保护残仓期末{continuous_fixed.loc['p50_1_be','final_balance']:.2f}U，平70%期末{continuous_fixed.loc['p70_1_be','final_balance']:.2f}U。四种冻结退出分别配合两种倍率、两种复位方式，共16个连续加码账户，期末仅{escalating.final_balance.min():.2f}—{escalating.final_balance.max():.2f}U；这是容量约束下的研究账户余额，不是交易所强平金额。

净胜率提高没有抵消小盈利、费用及交易笔数变化。连续基线每笔平均毛收益{continuous_fixed.loc['whole_3r','mean_gross_r']:.3f}R、研究成本约{continuous_fixed.loc['whole_3r','mean_gross_r']-continuous_fixed.loc['whole_3r','mean_net_r']:.3f}R；平70%组毛收益仅{continuous_fixed.loc['p70_1_be','mean_gross_r']:.3f}R。0.5R平半仓在开发期把完整初始止损最长压到6，但净亏最长仍36；不能把这个历史完整止损数字当作未来或账户连亏保证。

## Owner观察与原始证据

Owner询问：“针对这个倍投……3min……是否可以分批止盈……很多浮盈变止损……或者移动止损调整一下”。此前已明确授权自行优化参数，本轮固定入场与成本，只研究退出；资金倍率在退出冻结后比较。

开发期2023-08至2024-12，原V8自然平仓744笔。其中记录达到至少1R浮盈后最终初始止损105笔；达到0.5R后初始止损198笔，达到1.5R后46笔，达到2R后15笔。MFE不含止损退出bar新增极值，属于旧口径保守记录，不能直接当作分批可成交次数。因此本轮重新按OHLC事件先后回放，未把旧MFE直接套成新盈利。

## 半仓兑现与整笔收益

若先平比例q，成交在tR，残仓最终在sR，整笔毛收益R=q×t+(1−q)×s；扣费净R还需减去整笔成本c。若本轮此前净亏损为D、当前全笔价格风险B U，回本要求残仓保护s≥(D/B+c−q×t)/(1−q)。这只是金额门槛，价格触及与实际成交都不保证。

| 1R先平后的处理 | 整笔毛R | 剩余最终到3R时整笔毛R |
| --- | --- | --- |
| 平50%，残仓仍止损−1R | 0 | 2 |
| 平50%，残仓开仓价退出 | 0.5 | 2 |
| 平50%，残仓在+0.5R退出 | 0.75 | 2 |
| 平70%，残仓开仓价退出 | 0.7 | 1.6 |

先兑现可以减少回吐，也降低最后走到3R时整笔利润。不能把“半仓3R”当“整笔3R”，也不能只看首批止盈是否命中就认定回本。

真实退出费用按成交数量与价格计算；本轮保留项目20bp名义总成本，按份额分摊，不对每次减仓重复扣整笔成本。[OKX费用说明](https://www.okx.com/en-gb/help/how-to-calculate-the-contract-transaction-fee)

## 预登记、数据与成交顺序

仅已有OKX ETH-USDT-SWAP三分钟上下文，482176bar，2023-07-31 11:12至2026-04-30 23:57 UTC；交易从2023-08-01开始。开发截至2025-01-01，冻结后评估2025年及2026年前4月，连续账户另列。所有这些历史曾被使用，不称全新盲测。**本配置holdout消耗0次**。

V8原V6准入＋V7压缩门＋同向绳索距离≤3ATR、5bar初始结构/0.2ATR缓冲/2ATR下限、原V6反向下一开盘保持。基线全仓3R，收盘2R启动4ATR跟踪；另设全仓1R移开仓价对照。新组一次只改变对照中的分批比例、首次目标、残仓保护或ATR跟踪距离。联合候选已由Owner优化授权并预登记，不再改入场。

partial与最终目标预置；无法确认同bar先后时旧止损先；已知开盘跨目标先处理该开盘成交，既有止损在开盘也被触发时按保守止损优先。分批后残仓仍占单仓，不接另一笔V8；保护在bar收盘后从下一bar生效，可能跳空滑过。反向信号处理剩余仓位。费用覆盖保护覆盖的是本笔，不自动覆盖前几笔债务。

实际交易应核对减仓后的止损数量；Reduce Only限制订单只减现有仓位。触发价与成交价可能不同，图表也可能与所选标记/指数触发价不同。[OKX分批TP/SL说明](https://www.okx.com/en-gb/help/how-do-i-modify-take-profit-tp-and-stop-loss-sl)

## 开发期13组：固定价格风险1U

每组从1000U开始。原始止损指整笔尚未减仓时命中初始止损；毛亏与净亏分别计数，价格保本不冒充净盈利。随机对照为同ETH/月份/方向/此前120bar ATR比例桶，每成交匹配9次同退出/成本；只比较自然退出子样本，不是可交易随机资金账户。
'''
    def add_rows(t,with_cash=False):
        headers=['退出']+(['资金方式'] if with_cash else [])+['期末U','笔数','净胜率','均值毛／净R','分批触发','完整初始／毛亏／净亏最长','随机均值R','超额R','月块p']
        rows=[]
        for r in t.itertuples():
            rows.append([NAMES[r.name]]+([r.cash] if with_cash else [])+[f'{r.final_balance:.2f}',r.n_natural,
                f'{100*r.win_rate:.2f}%',f'{r.mean_gross_r:.3f}/{r.mean_net_r:.3f}',r.n_partial,f'{r.max_full_initial_stop}/{r.max_price_loss}/{r.max_consecutive_net_loss}',
                f'{r.random_mean_net_r:.3f}',f'{r.excess_net_r:+.3f}',f'{r.p:.4f}'])
        return table(headers,rows)
    text+=add_rows(joined[(joined.period=='development')&(joined.cash=='fixed')])
    for period,title in [('validation','退出冻结后：2025年'),('preholdout','退出冻结后：2026年前4月')]:
        text+='\n## '+title+'\n\n每段独立以1000U开始，不补充本金；表内固定1U，选中退出与固定参考在开发后冻结。\n\n'
        text+=add_rows(joined[(joined.period==period)&(joined.cash=='fixed')])
    text+='\n## 连续账户：退出与加码一起看\n\n2023-08至2026-04只投入一次1000U。fixed为固定1U；x1.5/x2为亏后风险倍率，net_win为整笔净赚后复位，recovery为本轮累计净回本后复位。最多6个净亏层、10倍名义敞口容量；容量失败认亏后下一信号从1U重新开始，亏损不清账。仓位完全退出后才判断该笔是否触发加码。\n\n'
    text+=add_rows(joined[joined.period=='continuous_pre'],True)
    text+='\n## 回本、回撤与容量\n\n'+table(['退出','资金','现金回撤','最高风险U','容量拒单','净回本轮','认亏轮'],
        [[NAMES[r.name],r.cash,f'{100*r.max_realized_drawdown:.2f}%',f'{r.max_risk:.2f}',r.n_rejected_capacity,r.recovered_cycles,r.abandoned_cycles]
         for r in s[s.period=='continuous_pre'].itertuples()])
    text+=f'''
## 验证、归因与诚实声明

开发基线固定1U的751笔及479.821007U与上一轮同退出结果一致；每个账户都核对逐笔现金和周期现金总和。新引擎另用合成路径检查多空分批比例、同bar歧义、跳空、下一bar保护、反向残仓、边界和成本分摊。具体测试收据在results/validation.json；不得把未运行的测试记为通过。

实际定向测试127通过。全套boundaries/causality/parity为468通过、7失败：5项被既有TOTAL2产物缺source_commit阻断，另2项为本轮未改动的candidates.py/render.py迁移哈希漂移。注册契约12通过、4失败，来自同一TOTAL2记录及既有MA120实验缺source_commit。两条缺字段记录在本轮builder提交中已存在；render.py在该提交时已漂移，candidates.py为本轮未纳入提交的工作区改动。新实验与产物两行单独通过真实契约解析和交叉链接；未篡改旧记录或迁移账本使全套变绿。

与基线的差异归因需要区分：提前兑现可降低曾达目标后回吐的损失，同时截走大赢家的一部分收益；收紧ATR会更早退出但也可能更早退出后续趋势。提前退出也释放单仓名额，改变后续可成交笔数；开发全仓基线751笔，而1R平70%组775笔，平均净R近似但总亏损更大，不能把总额差异全部解释为单笔退出价。所有表展示净结果、完整止损及相同退出随机对照，不以首批命中率替代整笔盈利。对加码的裁决必须对照同退出fixed账户，不能把不同退出的差额全归因为倍率。

分批现金先到账，但本研究直到整笔平仓才更新可开下一笔的账户状态，单仓约束下没有利用中途释放现金开新仓。现金回撤按整笔退出/边界估计清算权益，不是持仓内峰谷；边界累计部分已实现收益与残仓标记分别保留，边界不计自然胜率/连亏。未重建标记价格强平、分档维持保证金、完整资金费、真实盘口滑点或最小张数，不能以不归零称安全。

无训练或分类器，也无事前排序分数，AUC与top-decile收益不适用；不事后挑盈利前10%冒充排序收益。替代对照为固定退出基线、同退出固定风险、同币/月/方向/因果波动桶随机入场和月份区块符号置换（9999次）。开发p受选择影响，2026年只有4个月，均不能轻易作确认性证据。本轮没有新增单特征，故全仓原退出是无新增变量基线。

全部匹配尝试{int(c.attempts.sum())}次，其中边界删失再抽{int(c.rejected_censored.sum())}次、无效再抽{int(c.rejected_invalid.sum())}次；条件自然退出的抽样限制已保留。正式门测试状态见validation.json，无关既有注册错误不得掩盖。

## 复现与产物

```bash
# builder/config必须先提交
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --capture=no tests/evaluation/test_spike_partial_exit.py
.venv/bin/python -m yoyo.evaluation.spike_partial_study develop
# 提交results/selection.json后再运行
.venv/bin/python -m yoyo.evaluation.spike_partial_study evaluate
.venv/bin/python scripts/report_spike_partial.py
```

输出目录拒绝覆盖。源context仍为原SHA绑定pre-May缓存，未复制或修改行情。results下保存13开发组、冻结配置、所有窗口/资金表、逐笔份额成交与匹配对照；manifest.json核对哈希。策略仅为研究候选，production_eligible=false、training_eligible=false。新配置最终holdout或实盘仍需Owner明确批准。

## 下一步边界

本轮结论为拒绝将这13组退出作为倍投可行性的依据。可复用的是份额成交账本和整轮债务公式；没有证据可给出实盘最优参数。若继续研究，应先独立验证入场扣费后的优势或整轮净债务保护假设，再考虑加码。修改固定研究成本或新增holdout评估需Owner决策，未在本轮执行。
'''
    report=ROOT/'analysis/p1_spike_eth3m_partial_tp_20260914.md';report.write_text(text)
    subprocess.run(['.venv/bin/python','scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)


if __name__=='__main__':
    main()
