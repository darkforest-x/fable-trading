# Local Signal V2 B2：候选密度与收益诊断

生成时间：2026-08-10T17:07:16.250146+00:00
结论等级：开发期密度与反事实收益诊断，不是生产回测。

## Executive Summary

- **口径纠正：上一版把3,880写成“交易/开单”是错误的。** 它们是B2在v10预筛proposal ledger上的L1 fire rows，不是订单。连续市场L1触发量与可执行订单数均未计算。
- **但B2检测密度本身确实过高。** conf=0.35命中3,880/7,795个预筛候选（49.78%），折合proposal ledger 88.27 fires/日；P1 easy negatives也命中56/357（15.69%）。
- **不是重复、edge或图像传输bug。** candidate_id唯一，同币最小间隔18根，edge2=edge3；8个样本的数组与PNG推理框数/数值完全一致。
- **提高阈值不能解决。** conf=0.45虽把proposal ledger密度降到8.35 fires/日，但正例召回从73.46%塌到6.98%。
- **阶段纠正：B2当前密度失败。** 下一步是交接规范中的P2 hard-negative mining + 连续因果盘口密度回放；P3 LightGBM/规则判断层在L1密度可信前阻断。

## 数量口径与密度

| 粒度 | 数量 | 含义 |
|---|---:|---|
| P1平衡endpoint尺 | 715 | 358正例 + 357 easy negatives；抽样验证，不是连续市场 |
| v10预筛proposal rows | 7,795 | 230币、已预筛且同币至少间隔18根 |
| B2 L1 fire rows | 3,880 | YOLO候选命中，不是订单 |
| 唯一outcome event groups | 3,715 | 事件组去重只减少4.25% |
| 连续市场L1 fires | 未测 | 未逐币×逐盘口endpoint扫描 |
| 可执行订单 | 未计算 | P2密度未过，P3判断与执行被阻断 |

| conf | 正例召回 | easy-neg命中率 | proposal fires | fires/日 |
|---:|---:|---:|---:|---:|
| 0.35 | 73.46% | 15.69% | 3,880 | 88.27 |
| 0.40 | 43.30% | 6.16% | 1,995 | 45.38 |
| 0.45 | 6.98% | 0.84% | 367 | 8.35 |
| 0.50 | 2.23% | 0.00% | 107 | 2.43 |

阈值梯度只用于定位问题，未修改冻结阈值。把密度压到个位数/日时召回只剩约7%，所以应通过hard negatives改变模型区分力。

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

## 反事实收益结果

以下结果只是假设“把每个L1 fire row都强行当作short候选”的结果诊断，不是订单回测。

| 范围 | 候选行 | 毛均值bp | 10bp净均值 | 10bp PF | 20bp净均值 | 20bp PF | 20bp胜率 | 单位和最大回撤(20bp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 未过滤池 | 7,795 | 1.60 | -8.40 | 0.910 | -18.40 | 0.816 | 32.03% | -15.409 |
| B2 edge3 去重 | 3,880 | 0.81 | -9.19 | 0.893 | -19.19 | 0.793 | 31.62% | -7.461 |
| B2 edge2 敏感性 | 3,880 | 0.81 | -9.19 | 0.893 | -19.19 | 0.793 | 31.62% | -7.461 |
| conf 最高10%（诊断） | 388 | 43.25 | 33.25 | 1.274 | 23.25 | 1.182 | 36.34% | -0.795 |

> 最大回撤是按时间排序的“每候选单位收益累加”回撤，不是仓位化资金曲线。候选结果可能重叠，不能解释为可同时执行组合。

## 匹配随机对照

| 范围 | L1 fire rows | 已匹配 | 覆盖率 | 模型20bp净均值 | 随机20bp净均值 | 超额bp | 周块p | 周块 |
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
| 单特征基线 | N/A：P3判断层尚未训练；主基线为未过滤冻结短向候选池 |

## 结果解读

1. 当前首要失败是L1密度：B2在已预筛proposal pool仍命中49.78%，且easy-negative endpoint命中率15.69%。
2. 若把每个fire强行当short，其10bp净均值为-9.19bp，且比未过滤池低-0.78bp/候选；收益诊断也不支持推进。
3. confidence高分段只是事后线索，四分位不单调且跨周不稳定，不能据此抬conf或进入P3。
4. 正确方向是P2 hard-negative mining先修L1区分力，并在独立时间块跑连续tip密度；不是直接训练判断层。

## 数据文件

- `analysis/output/p1_b2_short_l2_backtest_20260811.json`：完整汇总与协议。
- `analysis/output/p1_b2_density_diagnostic_20260811.json`：密度、阈值梯度与实现排错证据。
- `analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv`：7,795 个逐候选 B2 预测。
- `analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv`：3,880 个 L1 fire rows。
- `analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv`：3,666 个匹配候选结果。
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

PYTHONPYCACHEPREFIX=/tmp/fable_pycache PYTHONPATH=.:../yoyo-trading \
  .venv/bin/python scripts/audit_local_signal_v2_b2_density.py \
  --device mps --transport-samples 8

PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/build_p1_b2_short_l2_backtest_report.py

python3 scripts/md_to_html.py \
  analysis/p1_b2_short_l2_backtest_20260811.md --out-dir analysis/html
```

## 风险与诚实声明

- 本轮未跑连续市场扫描，88.27 fires/日只描述v10 proposal ledger，不能外推为订单/日。
- proposal pool本身已经预筛；但在这个富集池仍命中近半，已足以判定当前B2密度不可接受。
- B2 权重和 conf=0.35 来自 P1 开发期选择；已额外排除同币所有 val 端点前后72 bars，但剩余数据仍不是最终确认集。
- 置信度四分位和最高10%是事后诊断，禁止自动修改阈值。
- outcome可能时间重叠；把每个fire当short只是反事实诊断，周块检验只有7块。
- 未读取 holdout，未修改成本/障碍/新鲜度门，未 promote、未部署、未下单。

## 下一步

1. 当前B2按密度失败处理：不promote，不进入P3判断/执行。
2. 按交接规范执行P2 hard-negative mining：固定B2 30根窗口、当前事件尺与训练配方，只新增难负例。
3. 在不读holdout的独立时间块做连续因果tip endpoint密度回放；先冻结L1密度门、event匹配与去重口径。
4. 只有P2密度和事件门通过，才进入P3 LightGBM/规则判断层。禁止用提高conf代替重训。
