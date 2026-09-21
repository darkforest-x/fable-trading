# SPIKE High-R V1

Python 离线研究版本，针对高R利润兑现，只更换多头追踪锚点。当前结果以最终研究报告为准，不是默认策略或100U滚仓资金报告。

**结果：拒绝此候选。** 全3,531流完成，净>10R多单367→259，净>20R100→57；胜率提高却损失大趋势。后段仍亏，原版保留。正式输出run_v2/statistics_v1，run_v1为先前3流功能检查。完整原因为下方研究报告。

- 实现：`yoyo/evaluation/spike_high_r_v1.py`
- 固定规则：`PROJECT_PLAN.md`、`config.json`
- 回放：`yoyo/evaluation/spike_high_r_study.py`
- 统计：`yoyo/evaluation/spike_high_r_report.py`
- 报告：`analysis/p1_spike_high_r_20260921.md`
- Notion：[研究记录](https://app.notion.com/p/3e28856479af81e0bf7ed272ebb818a0)

## 复现

需要仓内已有、通过哈希认证的原V9及原始冻结cache，不重新下载或写入行情。

```bash
.venv/bin/python -m pytest tests/evaluation/test_spike_high_r_v1.py tests/evaluation/test_spike_high_r_study.py -q
.venv/bin/python -m yoyo.evaluation.spike_high_r_study --output experiments/active/exp-spike-high-r-20260921-v1/run_reproduce --workers 3
.venv/bin/python -m yoyo.evaluation.spike_high_r_report --run experiments/active/exp-spike-high-r-20260921-v1/run_reproduce --output experiments/active/exp-spike-high-r-20260921-v1/statistics_reproduce
```

先提交代码再运行。回放可在来源、规则和产物哈希完全相同的前提下续跑；统计输出目录必须全新。

## 规则

仅多头在原收盘2R条件启用后，保护价逐根取max(上一保护价，持仓已知最高high减4倍当前ATR后按tick向下取整)。当前bar先检查旧保护，收盘后才更新下根保护；跳空按真实open模型成交。原初始止损、入场、空头、反向退出、20bp成本不变。

原双向串行占仓完整保留，主指标统计多头。随机对照采用相同版本退出。账本MFE通常不含退出bar；其毛浮盈不可等同净R或可成交峰值。没有保证金、资金费、盘口深度或实盘强平认证。
