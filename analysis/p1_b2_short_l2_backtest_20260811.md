# Local Signal V2 B2：pre-holdout 短向候选池经济回放

生成时间：2026-08-10T17:07:16.250146+00:00
结论等级：开发期经济可行性诊断，不是最终确认，不是生产回测。

## Executive Summary

- **B2 单独不能作为交易策略。** 去重后 3,880 笔，10bp 后均值 -9.19bp、PF 0.893；20bp 后 -19.19bp、PF 0.793。
- **没有改善未过滤池。** 10bp 后 B2 比未过滤短向候选池低 -0.78bp/笔。
- **匹配超额未获统计支持。** 同币×同月×ATR 桶对照超额 +2.18bp，7 周块精确双侧 p=0.891。
- **最高置信度 10% 是线索，不是门。** 388 笔在 20bp 后 +23.25bp、PF 1.182，但匹配 p=0.453，不满足 p<0.01。
- **项目方向不变。** B2 只做 L1 候选生成；是否可交易必须由独立时间切分的 P2 判断层证明。

## 数据与协议

| 项目 | 值 |
|---|---:|
| 原始 pre-holdout short-L2 行数 | 8,553 |
| 排除 B2 val 端点 ±72 bars | 758 |
| 最终可回放候选 | 7,795 |
| 币种 | 230 |
| 信号时间 | 2026-03-20 06:15:00+00:00 — 2026-05-03 05:15:00+00:00 |
| 最晚 outcome end | 2026-05-03 22:45:00+00:00 |
| holdout 起点 | 2026-05-04 00:00:00+00:00 |
| holdout 读取 | 0 次 / False |
| B2 | fixed 30 bars, conf=0.35, edge3 |
| 方向 | short（冻结 L2 pool 提供，YOLO 不判方向） |
| 入场 / 出场 | next open / TP5 ATR、SL2 ATR、最长72 bars |
| 成本 | 10bp swap taker；20bp 保守报告敏感性 |

## 主要结果

| 范围 | n | 毛均值bp | 10bp净均值 | 10bp PF | 20bp净均值 | 20bp PF | 20bp胜率 | 单位和最大回撤(20bp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 未过滤池 | 7,795 | 1.60 | -8.40 | 0.910 | -18.40 | 0.816 | 32.03% | -15.409 |
| B2 edge3 去重 | 3,880 | 0.81 | -9.19 | 0.893 | -19.19 | 0.793 | 31.62% | -7.461 |
| B2 edge2 敏感性 | 3,880 | 0.81 | -9.19 | 0.893 | -19.19 | 0.793 | 31.62% | -7.461 |
| conf 最高10%（诊断） | 388 | 43.25 | 33.25 | 1.274 | 23.25 | 1.182 | 36.34% | -0.795 |

> 最大回撤是按时间排序的“每笔单位收益累加”回撤，不是仓位化资金曲线。候选结果可能重叠，不能解释为可同时执行组合。

## 匹配随机对照

| 范围 | 信号 | 已匹配 | 覆盖率 | 模型20bp净均值 | 随机20bp净均值 | 超额bp | 周块p | 周块 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B2 全部 edge3 | 3,880 | 3,666 | 94.48% | -17.46 | -19.65 | 2.18 | 0.891 | 7 |
| conf 最高10%（诊断） | 388 | 377 | 97.16% | 24.93 | -17.30 | 42.24 | 0.453 | 7 |

## 月度拆分

| UTC月 | n | 毛均值bp | 10bp净均值 | 20bp净均值 | 20bp PF | 20bp胜率 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-03 | 1,125 | -1.84 | -11.84 | -21.84 | 0.753 | 33.24% |
| 2026-04 | 2,500 | 5.18 | -4.82 | -14.82 | 0.845 | 32.08% |
| 2026-05 | 255 | -30.30 | -40.30 | -50.30 | 0.382 | 20.00% |

2026-05 只含 5 月 1–3 日，不与完整月份等权比较。所有月段在 20bp 后均为负。

## B2 confidence 分层

| 分位 | conf范围 | n | 毛均值bp | 10bp净均值 | 20bp净均值 | 20bp PF |
|---|---:|---:|---:|---:|---:|---:|
| Q1 | 0.3501–0.3810 | 970 | -4.70 | -14.70 | -24.70 | 0.731 |
| Q2 | 0.3810–0.4010 | 970 | 5.99 | -4.01 | -14.01 | 0.826 |
| Q3 | 0.4011–0.4233 | 970 | -14.34 | -24.34 | -34.34 | 0.611 |
| Q4 | 0.4233–0.5527 | 970 | 16.30 | 6.30 | -3.70 | 0.966 |

四分位不单调。最高10%阈值 0.4488 是看到本回放结果后的诊断切点，不是可写回生产的阈值。

## 项目必报指标对照

| 指标 | 本轮结果 |
|---|---|
| val AUC | N/A：B2 是固定阈值 YOLO 检测器，本回放没有 LightGBM 排序分数 |
| top-decile 毛 / 10bp净 / 20bp净 | 43.25 / 33.25 / 23.25 bp（仅 detector confidence 诊断） |
| top-decile 20bp胜率 / PF | 36.34% / 1.182 |
| top-decile 匹配随机超额 / p | +42.24bp / 0.453，不满足 p<0.01 |
| 单特征基线 | N/A：P2 尚未训练；主基线为未过滤冻结短向候选池 |

## 结果解读

1. B2 在全部固定阈值开火上没有经济选择力：比未过滤池还低 -0.78bp/笔。
2. Q4 与最高10%显示 B2 confidence 可能携带排序信息，但四分位不单调、跨周不稳定，证据不足以单独交易。
3. 正确方向是让 P2 在独立时间切分上判断 B2 候选，而不是围绕本回放继续调 YOLO conf。

## 数据文件

- `analysis/output/p1_b2_short_l2_backtest_20260811.json`：完整汇总与协议。
- `analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv`：7,795 个逐候选 B2 预测。
- `analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv`：3,880 笔主结果。
- `analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv`：3,666 笔匹配对照。
- `analysis/output/p1_b2_short_l2_backtest_report_20260811/daily.csv`：日度汇总。
- `analysis/output/p1_b2_short_l2_backtest_report_20260811/symbol.csv`：币种汇总。

## 完整复现命令

```bash
cd /Users/zhangzc/fable-trading
PYTHONPYCACHEPREFIX=/tmp/fable_pycache PYTHONPATH=.:../yoyo-trading \
  .venv/bin/pytest -q tests/test_backtest_local_signal_v2_b2_short_pool.py

MPLCONFIGDIR=/tmp/mplconfig PYTHONPYCACHEPREFIX=/tmp/fable_pycache \
  PYTHONPATH=.:../yoyo-trading .venv/bin/python -u \
  scripts/backtest_local_signal_v2_b2_short_pool.py --device mps --batch 12

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_p1_b2_short_l2_backtest_report.py

python3 scripts/md_to_html.py \
  analysis/p1_b2_short_l2_backtest_20260811.md --out-dir analysis/html
```

## 风险与诚实声明

- 本轮是 B2 × 冻结 short-L2 候选池回放，不是全市场逐 bar 扫描，也不是 B2+LightGBM 端到端回测。
- B2 权重和 conf=0.35 来自 P1 开发期选择；已额外排除同币所有 val 端点前后72 bars，但剩余数据仍不是最终确认集。
- 置信度四分位和最高10%是事后诊断，禁止自动修改阈值。
- outcome 可能时间重叠；周块检验只有7块，统计功效有限。
- 未读取 holdout，未修改成本/障碍/新鲜度门，未 promote、未部署、未下单。

## 下一步选项

1. **建议：进入 P2 开发，但不晋升 B2 为交易策略。** P2 必须严格时间切分，并把 B2 confidence 仅作为一个候选特征。
2. 若要单独验证“最高10%”假设，需 owner 先冻结阈值与新时间块；不能复用本回放作确认。
3. 不建议继续围绕 B2 conf 调参；当前证据最需要的是判断层选择力，而不是再做检测层经济拟合。
