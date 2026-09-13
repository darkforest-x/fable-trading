"""Render final receipt-bound A-share results as Markdown and HTML artifacts.

Only completed study summaries and their trade/coverage receipts are read.
This reporting step does not recompute indicators or rerun strategy outcomes.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from yoyo.evaluation.spike_ashare_data import save
from yoyo.evaluation.spike_ashare_study import EXP, ROOT, STATIC, sha


def deliver(exp=EXP):
    exp=Path(exp);out=exp/'results'
    summary=json.loads((out/'summary.json').read_text())
    if summary['status'] not in ('complete','incomplete'):
        raise ValueError('report requires a finished collection/evaluation pass')
    def n(value,percent=False):
        if value is None:return '不适用／样本不足'
        return f'{value*100:.3f}%' if percent else f'{value:.4f}'
    rows=[]
    for g in summary['groups']:
        rows.append('| '+' | '.join([g['version'].upper(),g['timeframe'],str(g['ready_symbols']),
            str(g['signals']),str(g['closed_trades']),str(g['open_trades']),n(g['win_rate'],True),
            n(g['profit_factor']),n(g['mean_gross_return'],True),n(g['mean_net_return'],True),
            n(g['sum_net_r']),n(g['random_mean_net_return'],True),n(g['excess_mean_net_return'],True),n(g['control_p_value'])])+' |')
    annual=[]
    for g in summary['annual']:
        annual.append(f"| {g['year']} | {g['version'].upper()} | {g['timeframe']} | {g['closed_trades']} | {n(g['win_rate'],True)} | {n(g['mean_net_return'],True)} | {n(g['sum_net_r'])} |")
    stem='p1_spike_ashare_v1_v8_three_year_20260913'
    md=ROOT/'analysis'/f'{stem}.md'
    text=f'''# SPIKE V1 / V8：沪深主板近三年日线与周线

固定四组评估已完成本轮可用数据处理。目标 **{summary['universe_count']}** 只，实际覆盖 **{summary['covered_symbols']}** 只，缺数或隔离 **{summary['failed_symbols']}** 只。结果状态：**{summary['status']}**。未就绪证券保留在分母中；这是历史研究，未通过实盘准入。

## 冻结口径与授权

- 收益窗口：{summary['start']} 至 {summary['end']}，交易日日历为 BaoStock 0.9.3；2008 年起的数据仅为预热。
- 当前 SPIKE V1 与 V8 的原有入场条件保留，普通主板账户仅做多。V8 保留 BB200、前 500 根宽度阈值、前 12 根中的连续 3 根压缩和距六均线边缘不超过 3 ATR；原始 V6 反向确认仍可退出。
- 结构止损最近 5 根减 0.2 ATR、至少 2 ATR 风险底线，2R 收盘后激活 4ATR 跟踪；下一真实交易日开盘入场，T+1 与涨跌停按日撮合，周线跟踪只在周收盘更新。
- Owner 于 2026-09-13 明确授权四组各评估一次。**V1 日线、V1 周线、V8 日线、V8 周线：这是各配置第 1 次消耗本次 A 股 holdout。** 不重读已完成流作参数试验，不把旧加密资产发现称为本次盲测。

## 版本同表对照

PF 按自然平仓净 R 的盈利和亏损计算；平均收益为独立事件均值，累计 R 不代表共享资金账户。期末未平仓从胜率、PF 和已实现累计 R 排除。

| 版本 | 周期 | 就绪股票 | 信号 | 平仓 | 未结束 | 胜率 | 净R PF | 平均毛收益 | 平均净收益 | 累计净R | 随机净收益 | 池均值超额 | 聚类置换p |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

## 数据统计与时间分层

无训练、调参或随机 train/val 切分。四组为预先冻结的历史评价，按实际入场年度分层；全部自然平仓数就是各组的评价样本数，正类比例在这里明确定义为净收益大于零的平仓胜率。缺数、预热不足、随机配对缺额及不能成交的原因见覆盖文件。

| 入场年 | 版本 | 周期 | 自然平仓数 | 胜率 | 平均净收益 | 累计净R |
|---|---|---|---:|---:|---:|---:|
{chr(10).join(annual) if annual else '| 无 | 无 | 无 | 0 | 不适用 | 不适用 | 不适用 |'}

## 对照、指标适用性与解读

- 匹配随机对照：同一股票、同一季度、同一因果 ATR/close 四分位桶，固定种子且不放回抽取与策略信号等量的入场候选，使用同样的成交、止损、原始反向退出、周期跟踪和 0.2% 成本。无可配位置诚实计缺；串行持仓与涨跌停可使最终成交数不等。未凭结果选择随机种子。
- 聚类置换 p：对同时存在自然平仓的股票配对比较两侧平均净收益，用 9,999 次固定种子的符号置换检验超额是否为正。该统计按股票等权，表中池均值超额按已平仓事件等权，两者口径不同。样本不足返回不适用，不伪造显著性。
- val AUC：不适用，本轮没有拟合概率分类器或验证排序器。
- top-decile 毛/净收益与单特征排序基线：不适用，本轮执行的是两套冻结布尔信号，没有经过授权的排序变量；不能事后挑一列排名再冒充原策略。可比较的入场基线是同分层随机对照，毛/净事件收益同时列出。
- V1/V8 的信号定义和就绪时长不同，原始数量变化不能单独归因为过滤效果；V8 日/周更长的背景预热会减少可评估股票。周线事件持有更长、期末截尾更多时，应同时看未结束数量，不能把截尾收益当自然退出成绩。

## 风险与诚实声明

{chr(10).join('- '+w for w in summary['warnings'])}
- 全量目标是 BaoStock 历史名单与 IPO/退市基础资料的并集，仍受供应商历史退市数据质量约束；源记录不等于交易所逐笔成交真相。
- 本次不提供共享资金复利、容量、100 股手数账户、融资融券、分红送股现金到账或真实税费账；固定 0.2% 研究成本沿用原 SPIKE 契约，不能称实际 A 股账户净利润。
- 若结果覆盖不完整，必须带覆盖率阅读，不进行全主板盈利裁决。即使描述性指标为正，也不自动达到项目经济准入和前向验证标准。

## 复现命令

完整输入与逐流产物已冻结，重启命令会验收并复用已完成流，不能修改代码后覆盖原结果。原始请求收据、逐股票 normalized SHA 和 evaluation_identity.json 共同绑定输入与生成器。

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=/tmp/spike-ashare-v18-bs093 .venv/bin/python -m yoyo.evaluation.spike_ashare_data --config {exp.relative_to(ROOT)}/config.json --destination {exp.relative_to(ROOT)}/data --workers 4
.venv/bin/python -m yoyo.evaluation.spike_ashare_study --experiment {exp.relative_to(ROOT)}
.venv/bin/python -m yoyo.evaluation.spike_ashare_delivery
.venv/bin/python -m pytest tests/evaluation/test_spike_ashare_engine.py tests/evaluation/test_spike_ashare_study.py tests/test_spike_burst_replay.py -q
node --test tests/monitor/frontend_ashare.test.cjs tests/monitor/frontend_cards.test.cjs tests/monitor/frontend_theme.test.cjs
```

## 下一步

- 可以按年度、股票覆盖与无法成交原因阅读冻结结果，不改变参数。
- 新规则、成本、止损或再次使用 holdout 需 Owner 决策；不自动训练、promote、部署到信号扫描或实盘下单。

## 来源与产物

- [BaoStock 0.9.3 官方维护包](https://pypi.org/project/baostock/0.9.3/)
- [上交所 2026 交易规则与施行日期](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)
- [深交所 2026 交易规则](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)
- 逐笔：`{out.relative_to(ROOT)}/trades.csv.gz`；覆盖：`{out.relative_to(ROOT)}/coverage.csv`；生成器身份：`{out.relative_to(ROOT)}/evaluation_identity.json`。
'''
    md.write_text(text)
    subprocess.run([sys.executable,str(ROOT/'scripts/md_to_html.py'),str(md),'--out-dir',str(ROOT/'analysis/html')],check=True,cwd=ROOT)
    html=ROOT/'analysis/html'/f'{stem}.html'
    (STATIC/'report.html').write_bytes(html.read_bytes())
    summary['report_url']='/static/ashare-backtest/report.html'
    save(out/'summary.json',summary);save(STATIC/'summary.json',summary)
    files=[md,html,out/'summary.json',out/'trades.csv.gz',out/'coverage.csv',out/'evaluation_identity.json']
    receipt=dict(generated_at=summary['generated_at'],files={str(p.relative_to(ROOT)):sha(p) for p in files})
    save(out/'delivery_receipt.json',receipt)
    return receipt


if __name__=='__main__':
    deliver()
