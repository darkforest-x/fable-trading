"""Render the BE05 report from completed saved outcome aggregates, no OHLC reads."""
from pathlib import Path
import hashlib
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
DELIVERY = EXP / 'delivery_v1'
COMMON = EXP / 'common/results/full_v3'
REPORT = ROOT / 'analysis/p1_spike_v1_v8_be05_20260914.md'
NAMES = {'v1_native':'原版 V1（只做多）','v1_common':'V1 统一退出敏感性（只做多）','v8':'V8（多空）'}
PERIODS = {'full':'完整两年','earlier':'前一年，剔除跨切点退出','later':'后一年'}


def f(value, digits=2):
    return '—' if pd.isna(value) else f'{value:,.{digits}f}'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |'] +
                     ['| '+' | '.join(str(x).replace('|','/') for x in row)+' |' for row in rows])+'\n'


def main():
    metrics = pd.read_csv(DELIVERY/'metrics.csv', dtype={'timeframe_min':str})
    paired = pd.read_csv(DELIVERY/'paired_changes.csv', dtype={'timeframe_min':str})
    receipts = json.loads((COMMON/'manifest.json').read_text())
    native = json.loads((EXP/'native_v1/receipt.json').read_text())
    stream = pd.read_csv(COMMON/'stream_summary.csv')
    full = paired.query("period == 'full' and timeframe_min == 'all'").set_index('system')
    native_r, v8_r = full.loc['v1_native'], full.loc['v8']
    chunks = ['# V1 / V8：浮盈触及 0.5R 后推开仓价，效果如何',
        '研究日期：2026-09-14。唯一实验键：`exp-spike-v1-v8-be05-20260914-v1`。状态：历史敏感性研究，未接入线上。',
        '## 先看结果',
        f"原版 V1 在同一批 {int(native_r.joint_closed):,} 笔双方已结束交易上，累计净 R 从 {f(native_r.baseline_total_r)} 变为 {f(native_r.be05_total_r)}，变化 {f(native_r.delta_r)}R；V8 在 {int(v8_r.joint_closed):,} 笔相同入场上，变化 {f(v8_r.delta_r)}R。净胜率和大趋势保留率必须一起判断，不能只看累计 R。",
        '下面主表只比较相同入场、且两种退出都已有完整结果的交易，排除重新开仓与未结束样本变化。R 是每笔实际入场价到初始止损的价差单位。**累计 R 是事件账本之和，不是账户收益率，也不等于可实现的组合资金曲线。**',
        table(['系统','相同已结束笔数','累计净R 原→BE','净胜率 原→BE','PF(R) 原→BE','原本兑现≥10R 保留'],[
            [NAMES[s],int(full.loc[s].joint_closed),f'{f(full.loc[s].baseline_total_r)} → {f(full.loc[s].be05_total_r)}',
             f'{f(full.loc[s].baseline_win_rate*100)}% → {f(full.loc[s].be05_win_rate*100)}%',
             f'{f(full.loc[s].baseline_pf_r,3)} → {f(full.loc[s].be05_pf_r,3)}',
             f'{int(full.loc[s].retained_original_ge10)}/{int(full.loc[s].original_realized_ge10)}'] for s in ['v1_native','v8','v1_common']]),
        'V1 统一退出敏感性放在补充行：它沿用 V1 入场，但采用通用 ATR 跟踪与反向退出。它不是原版 V1，不能拿它替代原版结果。',
        '## 本次具体怎样推保本',
        '1. 信号保持冻结，实际按下一根开盘入场。初始 R 在实际入场时固定，之后不重新缩小分母。',
        '2. 持仓未被原保护位止损的情况下，已完成 K 线的最高价（多）或最低价（空）触及 +0.5R，才记录触发。这里不是用交易结束后的最终 MFE 倒推入场时就知道结果。',
        '3. 下一根开始保护价推到实际入场价；已经更紧的保护继续保留。当前根先检查原止损，不允许同根先看高点、再假装已经在开仓价离场。',
        '4. 保留原版退出。原版 V1 使用原生信号参考保护路径；V8 与统一退出 V1 保留初始5根结构/0.2ATR缓冲/最低2ATR风险、收盘浮盈2R启动4ATR跟踪、原始反向信号次开盘退出。',
        '5. 固定扣 0.20% 往返名义成本。开仓价退出仍为小亏：净R = −0.20% / 初始止损百分比。开盘跳过保护位按较差开盘价退出。',
        '这是一种收盘更新保护的规则，不能把结果等同于交易所逐笔实时触及0.5R立刻改单；后者要更细粒度数据才能验证。',
        '## 救回了多少亏损，又牺牲了多少趋势',
        table(['系统','减轻原亏损笔数','减轻亏损贡献R','伤害原盈利笔数','被削减盈利R','净变化R','每笔ΔR 周块95%区间'],[
            [NAMES[s],int(r.rescued_losers),f(r.rescue_delta_r),int(r.harmed_winners),f(r.harmed_delta_r),f(r.delta_r),
             f'[{f(r.mean_delta_weekly_ci_low,4)}, {f(r.mean_delta_weekly_ci_high,4)}]'] for s,r in full.iterrows()]),
        '“减轻原亏损”包含 −1R 变成手续费小亏，不表示变成盈利单。“大趋势保留”依据原来实际兑现净R≥10，而非盘中峰值≥10。周块区间以整周重采样，尽量保留同一时段多币、多交易所的相关性；这仍是反复研究过的历史，并非未来收益保证。',
        '## 周期差异：相同入场对照',
        table(['系统','周期','共同已结束','原累计净R','BE累计净R','变化R','原净胜率','BE净胜率','≥10R保留'],[
            [NAMES[r.system],r.timeframe_min+'m',int(r.joint_closed),f(r.baseline_total_r),f(r.be05_total_r),f(r.delta_r),
             f(r.baseline_win_rate*100)+'%',f(r.be05_win_rate*100)+'%',f'{int(r.retained_original_ge10)}/{int(r.original_realized_ge10)}']
            for _,r in paired.query("period == 'full' and timeframe_min != 'all' and system != 'v1_common'").iterrows()]),
        '## 时间差异：前后各一年',
        '切点为 2025-09-10 00:00 UTC。前段要求入场和双方退出均早于切点；后段按切点后入场。跨切点交易仅留在完整两年总表，因此两段未必加总等于完整表。未按前段结果再选0.5R，此阈值来自 Owner 本次问题。',
        table(['系统','阶段','共同已结束','原累计净R','BE累计净R','变化R','每笔ΔR 周块95%区间'],[
            [NAMES[r.system],PERIODS[r.period],int(r.joint_closed),f(r.baseline_total_r),f(r.be05_total_r),f(r.delta_r),
             f'[{f(r.mean_delta_weekly_ci_low,4)}, {f(r.mean_delta_weekly_ci_high,4)}]']
            for _,r in paired.query("period != 'full' and timeframe_min == 'all'").iterrows()]),
        '## 串行重放与未结束交易：不要混入主表',
        '提早保本退出会腾出空位，可能多开后续交易。下表列出按各自退出结果重新运行的串行版本，以及固定入场版本的各自完整账本。固定版本也可能将原本未结束的交易提前结束，所以笔数与主表不同。未结束数据不能记成已实现收益。',
        table(['系统','方式','规则','入场/结束/未结束','净R总和','净胜率','PF(R)','PF(名义收益)','事件回撤R'],[
            [NAMES[r.system],r['mode'],r.rule,f'{int(r.events)}/{int(r.closed)}/{int(r.censored)}',f(r.total_r),f(r.win_rate*100)+'%',f(r.pf_r,3),f(r.pf_nominal,3),f(r.event_drawdown_r)]
            for _,r in metrics.query("period == 'full' and timeframe_min == 'all'").iterrows()]),
        '事件回撤按退出时间排列累计R计算，未模拟账户保证金、并发仓位上限、复利和同币跨市场风险分配，不能换算成1000U账户回撤。',
        '## 极端盈利集中度',
        '原版 V1 在同一原已结束样本中，RAVE 的7笔贡献原 +1193.73R、BE +1194.85R。仅作事后集中度诊断移除这7笔，其余6109笔从 −339.03R 改为 −151.98R，仍为负。不能把这一诊断当成事先能选出RAVE或排除其他币的策略。',
        '## 覆盖、基线核对与失败记录',
        f"数据为 Binance / OKX / Gate 现有历史池，30m / 1H / 4H，2024-09-10（含）至2026-09-10（不含）UTC。共同执行与V8完整 {receipts['streams']:,} 条交易所×合约×周期流；原生V1 {native['paired_events']:,} 个冻结事件，{native['completed_streams']:,} 条有事件流，{native['source_files']:,} 个原始来源文件，原始基线逐笔核对全部通过。",
        table(['审计系统','归档→本次基线 变化事件','归档→本次基线 ΔR'],[
            [NAMES['v1_common' if arm=='v1_common_execution_long' else 'v8'],int(g.legacy_to_clean_event_count.sum()),f(g.legacy_to_clean_delta_net_r.sum(),8)]
            for arm,g in stream.groupby('arm')]),
        '本次失败过程保留：原生V1 sample_probe 缺字段失败；全量首次结束时汇总分组失败，之后修复重跑；共同执行先修复费用漏计、索引偏移、固定入场字段，再出现一次残留反向退出意图问题。初步误归因于归档V8，逐行检查发现归档 replay_policy 已正确清理，是本次新写分支漏清，现已修复。只用 full_v3 完整产物作为最终对照；full_v1/full_v2 不作为结果。',
        '本配置编号为1，但不是只读过一次历史：上述样本、失败重跑与修复都重复读取过重叠数据。Owner 已授权任意历史日期研究；2026-05-04后的数据已消耗，不能宣称全新盲测。没有搜索其他BE阈值。',
        '原生V1逐笔文件生成身份来自执行日志、git与落盘时间交叉核对：builder `3b9dd8d46ad955b7ac75bde4f4f345e6a8082a63`，落盘 2026-09-14 14:24:55 +08。其receipt未直接写入运行时Python版本和该commit，故这是重建身份，不冒充完整运行时签名。后续summary/RAVE统计只读取逐笔CSV。共同执行full_v3的代码/配置哈希见identity.json；汇总器哈希与所有输入哈希见aggregation_receipt.json。',
        '## 风险与诚实声明',
        '- 当前样本池不是各历史时点全部上市及退市合约的完整普查，有存活与覆盖偏差。跨所、跨周期交易可能高度相关。',
        '- 费用沿用统一0.20%假设，没有完整资金费率、盘口冲击与逐笔成交。同根价格路径未知；没有用高低点排列选择对自己有利的成交。',
        '- AUC、top-decile与随机入场alpha检验不适用于本次相同入场的退出干预，未计算也不编造。本次严格对照是同一事件原退出；周块区间只描述退出变化的不确定性，不能证明入场alpha。',
        '- 原生V1账本只做多；V8多空。此次没有补造一个“原生V1空头”版本。',
        '- 没有修改 Pine、线上监控、Bark、Telegram、自动化、ACTIVE或任何真实仓位。',
        '## 复现与查看逐笔数据',
        '以下命令在本仓根目录运行；原缓存必须存在并通过SHA核对。新输出目录不能覆盖已有冻结产物。',
        '```bash\n.venv/bin/python -m pytest tests/test_spike_native_v1_be05.py tests/test_spike_v1_v8_be05.py tests/test_spike_be05_report.py -q\n.venv/bin/python -m yoyo.evaluation.spike_native_v1_be05 --output experiments/active/exp-spike-v1-v8-be05-20260914-v1/native_reproduction\n.venv/bin/python -m yoyo.evaluation.spike_v1_v8_be05 --official --output experiments/active/exp-spike-v1-v8-be05-20260914-v1/common/results/reproduction\n.venv/bin/python -m yoyo.evaluation.spike_be05_report --common experiments/active/exp-spike-v1-v8-be05-20260914-v1/common/results/full_v3 --native experiments/active/exp-spike-v1-v8-be05-20260914-v1/native_v1 --output experiments/active/exp-spike-v1-v8-be05-20260914-v1/delivery_reproduction\n.venv/bin/python experiments/active/exp-spike-v1-v8-be05-20260914-v1/render_report.py\n.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v1_v8_be05_20260914.md --out-dir analysis/html\n```',
        '主报告使用既有 delivery_v1；新复现汇总输出到 delivery_reproduction 后可与原CSV逐列核对。',
        '- `delivery_v1/paired_trades.csv.gz`：同一入场的原退出与0.5R BE逐笔配对。',
        '- `delivery_v1/all_outcomes.csv.gz`：含固定/串行、已结束/未结束及全部系统身份。',
        '- `delivery_v1/metrics.csv` 与 `paired_changes.csv`：周期、年度、胜率、PF、R、尾部保留和差异分解。',
        '- `native_v1/rave_concentration.csv`：RAVE事后集中度诊断。',
        '官方机制说明：[TradingView strategies](https://www.tradingview.com/pine-script-docs/concepts/strategies/) 对历史OHLC成交假设和更低周期细化有说明；本报告使用Python重放，不宣称已完成TV逐笔原生成交验收。',
        '## 下一步决策',
        '不把0.5R推保本自动设成全局默认。根据原版与V8、周期、实际≥10R保留分别评估取舍；任何上线需要另行决定。若要研究盘中触发立即改单，应独立冻结规则并用更细数据复核，不拿本次次根生效结果冒充。']
    examples = pd.read_csv(DELIVERY/'delta_examples.csv')
    rows=[]
    for system,g in examples.groupby('system'):
        for _,r in pd.concat([g.nsmallest(2,'delta_r'),g.nlargest(2,'delta_r')]).iterrows():
            rows.append([NAMES[system],r.venue,r.symbol,int(r.timeframe_min),r.entry_time,f(r.net_r_baseline),f(r.net_r_be05),f(r.delta_r)])
    chunks += ['## 逐笔差异示例（事后选取，便于复盘）',table(['系统','交易所','合约','分钟','入场UTC','原净R','BE净R','变化R'],rows)]
    REPORT.write_text('\n\n'.join(chunks)+'\n',encoding='utf-8')
    print(REPORT)


if __name__ == '__main__':
    main()
