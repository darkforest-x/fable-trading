"""Owner-facing original V1 versus V3 explanation from frozen gate diagnostics.

Only renders existing results. No fitting, market reads, scoring or live writes.
The repository's required Markdown-to-HTML delivery remains canonical.
"""
from pathlib import Path
import json
import subprocess
import pandas as pd
from yoyo.evaluation import spike_v3_gate_diagnostic as d
from yoyo.evaluation import spike_burst_recall_study as old

NAMES={'baseline':'V3不加过滤','tag_recent_density':'近期密集','tag_above_six':'六线全上',
 'tag_original_volume':'单根量比≥4','tag_original_expansion':'单根TR/前ATR≥3',
 'tag_original_body':'阳线实体≥55%','tag_original_close_position':'收盘位置≥75%',
 'tag_nonnegative_md':'MD≥0','early':'预警','confirmed':'确认','full':'完整61日',
 'first31':'前31日','last30':'后30日','case_night':'手选启动夜'}


def table(frame,columns):
    lines=['| '+' | '.join(title for _,title,_ in columns)+' |','|'+'|'.join(['---']*len(columns))+'|']
    for _,r in frame.iterrows():
        vals=[]
        for key,_,fmt in columns:
            v=r[key]
            vals.append('—' if pd.isna(v) else NAMES.get(str(v),str(v)) if fmt=='s' else format(v,fmt))
        lines.append('| '+' | '.join(vals)+' |')
    return '\n'.join(lines)


def build():
    own=Path(__file__).resolve();rel=str(own.relative_to(d.ROOT))
    if subprocess.check_output(['git','show','HEAD:'+rel],cwd=d.ROOT)!=own.read_bytes():
        raise ValueError('Commit report builder first')
    manifest=json.loads((d.OUT/'manifest.json').read_text())
    for a in manifest['artifacts']: old.checked(a['path'],a['sha256'])
    d.authenticate()
    s=pd.read_csv(d.OUT/'summary.csv',float_precision='round_trip')
    full=s[s.period.eq('full')]
    early=full[full.arm.eq('early')];confirmed=full[full.arm.eq('confirmed')]
    columns=[('gate','单独加回的条件','s'),('valid','保留信号',',.0f'),('alerts_per_day','全池/日','.1f'),
        ('drop_fraction','提示减少','.1%'),('recall_1','原目标+1根覆盖','.1%'),
        ('natural_win_rate','自然平仓净胜率','.1%'),('peak_10r_retention','保留原10R峰值信号','.1%'),
        ('mean_excess_bp','相对匹配随机/bp','.1f')]
    phase_cols=[('arm','阶段','s'),('gate','条件','s'),('period','区间','s'),('valid','事件数',',.0f'),
        ('mean_gross_bp','事件毛均值/bp','.1f'),('mean_net_bp','事件净均值/bp','.1f'),
        ('paired_random_net_bp','匹配随机/bp','.1f'),('mean_excess_bp','配对超额/bp','.1f'),
        ('permutation_p','置换p','.4f'),('holm_p_13','13项校正p','.4f')]
    # Same pinned full-pool historical V1 rows are read only for this comparison.
    orig=pd.read_csv(d.PRIOR/'recall_comparison.csv')
    v1=orig[orig.arm.eq('v1')&orig.period.eq('full')].iloc[0]
    a=early[early.gate.eq('baseline')].iloc[0];b=confirmed[confirmed.gate.eq('baseline')].iloc[0]
    compare=pd.DataFrame([dict(version='原版SPIKE强劲爆发V1',signals=v1.signals,daily=v1.signals/61,recall=v1.recall_1),
        dict(version='V3预警',signals=a.valid,daily=a.alerts_per_day,recall=a.recall_1),
        dict(version='V3确认',signals=b.valid,daily=b.alerts_per_day,recall=b.recall_1)])
    text=f'''# V3 为什么信号太多，以及如何降噪

## 结论

**比较基准是原版「SPIKE强劲爆发V1」，本报告忽略中间版本。**V3早预警把“近零蓄势后强劲爆发”放宽成“突破前12根高点并站上两条快均线”；确认阶段也未恢复近零蓄势、整段箱体边界和原来的K线质量门槛。为了补漏，候选层被放宽过多，而且候选层默认带盈亏参考框，与Owner要的开仓级启动形态不一致。

单项实测显示：恢复原单根3ATR扩张最能减少确认提示（约80%），净胜率有所提高，但丢失约73%的原10R峰值事件，且仍未证明优于匹配随机。恢复密集、六线、量比或实体中的任意一条，也没有独立建立盈利优势。不能把历史某一项数字较好叫“最优参数”。

## 原版约每天1.4次，V3预警约170次

同一278个历史OKX合约、1H多头、UTC2026年7月10日至9月9日前，共61天。BTC/ETH沿用原排除。这是全池负担，不是单币每天170次。原目标分母固定1660个正例，定义见方法；高覆盖并非胜率。

{table(compare,[('version','版本','s'),('signals','信号数',',.0f'),('daily','全池平均/日','.2f'),('recall','原目标+1根覆盖','.2%')])}

V3两阶段不是可直接相加的独立机会：3809个子确认都来自已有预警，其中2153个同根合并展示、1656个延迟另画标签。因此主图有12042个不同信号bar，而不是14195个主图启动标签。在样本窗口内6577个预警没有后续确认；这个数包含期末可能尚未等满窗口的预警，不能全部称过期或亏损。

## 放宽了哪些条件

| 条件 | 原版V1 | V3预警 | V3确认 |
|---|---|---|---|
| 近零蓄势 | MD和信号线同时近零至少12根；第12根冻结带宽 | 不要求 | 不要求 |
| 六均线密集 | 发起启动时，前12根平均宽度≤3ATR且交织≥2次 | 不要求 | 前12根至少出现过一次密集状态 |
| 突破位置 | 整段蓄势箱体的冻结上沿 | 最近12根最高价 | 父预警时冻结的前12根高点 |
| 价格与均线 | 全部六线上方 | 仅SMA20、EMA20上方 | 不强制六线之上 |
| 成交量 | 当前单根量比≥4 | 不要求 | 三根平均量比≥1.5，基准窗口也不同 |
| 波动扩张 | 当前TR/前根ATR≥3 | 不要求 | 三根净涨幅/三根前ATR≥1.5 |
| K线质量 | 阳线，实体≥55%，收盘在当根振幅上方25% | 均不要求 | 均不要求 |
| IMACD方向 | 价格先行要求MD≥0；释放确认要求MD>0，另有主线/信号约束 | 不要求 | 仅MD≥SB、ZLEMA上升，MD可以仍在零下 |
| 正在趋势参考中 | 不再触发新启动 | 仍可预警，只有12根冷却 | 仍可确认，一个父预警最多一次 |
| 未确认时的盈亏框 | 对应已通过硬爆发条件的启动 | 空闲时第一条预警就开始画 | 确认不是创建参考框的必要条件 |

近零带是max(abs(MD),abs(SB))≤0.1×前根ATR，第12根后冻结。原版两条路径略有不同：价格先行须MD≥0、MD≥SB且ZLEMA上升；先释放后确认则须MD>0、MD>SB且MD较前根上升，并在释放当根至后5根的有效窗口内完成突破。不能把两个路径的所有门混成一个。

普通反弹、趋势中途创新高、长上影的弱突破都可能满足V3预警。12根价格新高不等于长期蓄势区突破；1H里只是突破前12小时。冷却12根也不等于出现了新的蓄势结构。上述是代码机制；各因素的贡献会重叠，不能把下面的过滤比例相加。

## 在V3预警上，分别恢复原版一条条件

每一行只加一条，均对照V3原预警。单根4倍量、3ATR扩张会大幅减少提示，但会同时损失大量早期目标。原版“完整近零蓄势→箱体释放”需要状态机，不能用某一根tag假装完整恢复。

{table(early,columns)}

净胜率是自然结束的独立事件扣20bp成本后为正的比例；未平仓的期末标记另列。低胜率本身不否定趋势策略，但需要足够的大盈利补偿；这里所有新单项的匹配随机超额仍为负。

## 在V3确认上，分别恢复原版一条条件

3ATR过滤把确认提示从62.4/日降到12.3/日，自然净胜率29.5%→35.4%；但原10R峰值信号只保留26.9%，且配对超额仍为−23.4bp。这个取舍说明仅追求大阳线可能变成“等涨起来才筛中”，不能作为无代价降噪。

{table(confirmed,columns)}

“原10R峰值信号保留率”指在相同止损/跟踪模拟下曾达到10R峰值的事件保留比例，不是兑现10R，也不等于不同币种/独立大行情数量。3ATR条件没有检测隐藏资金或庄家行为，只描述当前相对历史波幅。

## 截图中的好行情也会被简单加严挡掉

HYPE 8月19日21:00预警不满足原单根4倍量或3ATR，到次日00:00确认才满足。PEPE 21:00同根确认不满足六线全上、原4倍量、3ATR或75%收盘位置；盲目恢复任何一条都会丢掉这个早信号。NEAR较早弱预警导致冷却问题，单项显示过滤不会释放这段冷却，本轮不能声称修复。

因此不把所有原版门槛一次性加回。需要恢复的是“完整蓄势的资格”，并区分点火与后续趋势，而不是要求每个启动都长成巨量巨阳。

## 优先怎样降低噪音

1. **结构资格优先**：重建持续压缩/近零的完整区间，用其冻结边界判断第一次启动。密集形成的过程和持续性，与单次密集命中分开验证。
2. **趋势阶段分开**：蓄势、点火、持有、失效各有状态；趋势内普通新高更新跟踪，只有新的整理结构形成才重新允许启动。不能只延长时间冷却。
3. **确认K线质量作独立检验**：实体、收盘位置和相对扩张可减少弱突破，但本轮表明它们各自都不足以证明赚钱；高周期/全市场共振也是新假设，目前V3未用它们过滤。
4. **先修复“预警像开仓”的表达**：预警只观察，不默认生成入场/止损框；通过确认才建立自己的当根价格参考。这是显示语义修复，不等于提高胜率或已完成策略降噪。

本轮没有替换Pine、线上扫描、Bark、Telegram或订单规则；没有据已见结果直接选择一个单项部署。

## 分期和对照：不能只挑启动当晚

下表保留所有固定单项和四个时期。手选8月19日启动夜高度共振，只作描述；前31日和后30日也已见过，不是盲测。随机对照来自原V3每条事件，同币×UTC周×因果ATR桶，费用和障碍相同，不为本次过滤重新寻找更差对照。比较不同版本历史匹配随机均值不是相同随机日程的因果实验。

{table(s,phase_cols)}

## 方法、复现与风险和诚实声明

这是各固定过滤配置第1次消耗holdout（边界≥2026-05-04），沿用Owner所有日期研究授权。13项过滤统一Holm校正，全期校正p均为1；未建立相对匹配随机的正超额。没有训练，训练val样本/val AUC不适用。原8046个事件锚点中1660正例、6240负例、146未知；正例只定义为未来24根收盘先达+4ATR而非−2ATR，并不要求均线密集。+1覆盖分母不改，不能改分母制造达标。

筛选只用事件当根/之前的特征。原父子信号、冷却、出现时间和已计算交易结果冻结；移除一条预警后不会自动生成原来被冷却压住的信号，移除一个确认后不会向后补找新确认。因此是事件过滤诊断，不是完整新检测器回放。各单项效果可重叠，不能据此精确拆分全部新增信号因果贡献。

交易沿用实际次根开盘、原结构/ATR初始止损、2R激活4ATR跟踪、无固定止盈、20bp入场名义往返费用。资金费、冲击与完整滑点未建模。独立事件会重叠；没有账户最大回撤、杠杆收益或可交易容量结论。只评估1H多头，不外推其他周期/空头。低胜率可接受的前提仍是组合层面的盈亏补偿得到验证。

```bash
.venv/bin/python -m pytest -q tests/test_spike_v3_gate_diagnostic.py
.venv/bin/python -m yoyo.evaluation.spike_v3_gate_diagnostic
.venv/bin/python -m yoyo.evaluation.spike_v3_gate_report
```

先提交准确builder再运行。诊断拒绝覆盖已存在产物，已完成研究只核对结果，不能删除重刷。原数据/交易CSV只读并核验哈希。源码、计划、结果CSV、保留事件ID与证据清单在本实验目录。

## 单特征描述基线

下列AUC仅为当前保留事件量比/TR与净盈利的描述关系，不是训练val指标；最高10%按特征排序，不按未来盈利挑选。均列出匹配随机，不能只读绝对收益。

{table(pd.read_csv(d.OUT/'score_summary.csv'),[('arm','阶段','s'),('gate','过滤','s'),('feature','单特征','s'),('n','样本数',',.0f'),('descriptive_auc','描述AUC','.3f'),('top_gross_bp','前10%毛/bp','.1f'),('top_net_bp','前10%净/bp','.1f'),('top_win_rate','前10%净胜率','.1%'),('top_random_bp','匹配随机/bp','.1f'),('top_excess_bp','超额/bp','.1f')])}
'''
    md=d.ROOT/'analysis/p1_spike_v3_gate_diagnostic_20260910.md';md.write_text(text)
    subprocess.run([str(d.ROOT/'.venv/bin/python'),'scripts/md_to_html.py',str(md),'--out-dir','analysis/html'],cwd=d.ROOT,check=True)
    html=d.ROOT/'analysis/html'/f'{md.stem}.html'
    old.write_json(d.OUT/'report_manifest.json',dict(diagnostic_manifest_sha=old.sha(d.OUT/'manifest.json'),
        report_builder=old.artifact(own),outputs=[old.artifact(md),old.artifact(html)],
        source_notes={'audience':'owner, Chinese', 'delivery':'owner-required Markdown to HTML',
        'layout':'answer-first, exact comparison tables; no new time series requiring plots',
        'baseline':'original SPIKE V1 only; intermediate version excluded'}))


if __name__=='__main__': build()
