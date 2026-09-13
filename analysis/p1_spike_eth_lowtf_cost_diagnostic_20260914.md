# ETH V8 低周期成本预算与 1R 保本诊断

> 2026-09-14 · `exp-spike-eth-lowtf-cost-diagnostic-20260914-v1` · 既有冻结逐笔账本归约，不重放信号、K线、出场或账户。

ETH 3m 的“浮盈 1R 推保本”变差，主要不是保本成交的价格偏差，而是它截断了随后由 4ATR 跟踪止损兑现的尾部趋势；固定 0.2% 成本又使价格回到入场价仍可能是负 R。固定成本预算门 `fee_R≤0.5` 在两条 ETH 流的每笔表现上较原池好，但只是在已经成交的历史子集内描述：ETH 3m 后段从 -0.237R 到 +0.015R，同时丢失两笔已兑现 ≥10R，去掉最佳单后为 -7.55R；ETH 5m 两段仍为负。因此两者都不能直接作为线上规则。

## 范围、V8 身份与时间

所有基线行都来自冻结 V8 admission：V6 原始确认、V7 `prior_squeeze_run3`、同方向绳索距离不超过 3 ATR；信号确认后下一根开盘入场，前五根结构极值加 0.2ATR、至少 2ATR 风险，2R 后启用 4ATR 跟踪，原始 V6 反向事件仍可退出，往返名义成本固定 0.2%。

ETH 3m 是 OKX `ETH-USDT-SWAP`，开发段为 2023-07-31 11:12 UTC 至 2026-05-04，后段为 2026-05-04 至 2026-07-30 11:09；ETH 5m 是 Binance `ETHUSDT`，开发段为 2020-01-01 至 2024-01-01，后段为 2024-01-01 至 2026-05-01。交易所、时段和样本长度均不同，不能把 3m/5m 的差异解释为单纯周期因果效应。

本次只读取此前已授权、已研究的 `run_20260913_v3` 和 ETH 3m 1R 保本 `run_v2` CSV；没有读取 OHLCV，也不是新的盲验证或新的 OHLCV holdout 暴露。

## 冻结基线

PF(R) 是净 R 盈利和除以净 R 亏损绝对和；PF(价格收益)按净名义价格收益计算，不能混用。累计 R 是逐笔相加，不是账户资金曲线。

| 流 | 时段 | n | 胜率 | 合计/均值净R | PF(R) / PF(价格收益) | 已兑现≥10R | 去最佳单后净R |
|---|---:|---:|---:|---:|---:|---:|---:|
| ETH 3m OKX | 开发 | 1,460 | 24.32% | -909.02 / -0.623 | 0.446 / 0.542 | 9 | -937.24 |
| ETH 3m OKX | 后段 | 111 | 32.43% | -26.28 / -0.237 | 0.763 / 0.851 | 2 | -39.38 |
| ETH 5m Binance | 开发 | 1,261 | 25.69% | -433.92 / -0.344 | 0.633 / 0.739 | 8 | -452.87 |
| ETH 5m Binance | 后段 | 693 | 26.55% | -190.56 / -0.275 | 0.705 / 0.772 | 8 | -220.69 |

初始止损、反向 V6 次根开盘退出和跟踪止损均保留在逐笔输出中，并按方向、退出原因、确认月分表。大部分获利来自跟踪止损，初始止损则普遍为负；这说明低周期问题不只是成本，入场后缺乏足够延续也会直接进入初始止损。

## 固定成本预算门：`fee_R≤0.5`

定义为 `fee_R=0.002/initial_risk_frac≤0.5`，等价于冻结的实际 next-open 入场到初始保护线风险比例至少 0.4%。由于这个风险含下一根实际成交价，它只能被称为**入场时检查**，不能说在信号收盘已知。门是既有成交单的静态子集，不模拟过滤后释放资金会获得的新信号。

| 流 | 时段 | 原n / 合计R / 均R | 保留n / 胜率 | 保留合计R / 均R | PF(R) / PF(价格收益) | ≥10R保留 | 保留组去最佳单后R |
|---|---|---:|---:|---:|---:|---:|---:|
| ETH 3m | 开发 | 1,460 / -909.02 / -0.623 | 738 / 27.37% | -235.21 / -0.319 | 0.627 / 0.640 | 3/9 | -256.72 |
| ETH 3m | 后段 | 111 / -26.28 / -0.237 | 52 / 36.54% | +0.79 / +0.015 | 1.020 / 0.998 | 0/2 | -7.55 |
| ETH 5m | 开发 | 1,261 / -433.92 / -0.344 | 1,002 / 27.94% | -179.69 / -0.179 | 0.777 / 0.777 | 5/8 | -198.64 |
| ETH 5m | 后段 | 693 / -190.56 / -0.275 | 501 / 29.14% | -95.22 / -0.190 | 0.767 / 0.803 | 3/8 | -107.29 |

四个预先固定桶 `≤0.25`、`0.25–0.5`、`0.5–1`、`>1` 完整保留在 CSV，未从中再选阈值。表现随费用相对风险升高通常恶化，但不构成可上线的单调定律：ETH 3m 后段的 `≤0.25` 桶只有 9 笔，且成本门后段仍只覆盖 3 个 UTC 月。成本门后月均净R为正的月份分别是 ETH 3m 开发 5/34、ETH 3m 后段 2/3、ETH 5m 开发 11/48、ETH 5m 后段 11/28，稳定性不足。

## 为何 ETH 3m 的 1R 推保本更差

这部分使用 `run_v2` 中 1,460 个开发和 111 个后段的**相同入场逐笔配对**。规则是在有利触及 1R 的 K 线收盘后，从下一根开始把保护至少提到入场价；它不是同根即时 tick 保本，也不是 1R 止盈。

| 时段 | 原V8：n / 胜率 / 合计R / PF(价格收益) / ≥10R | 1R保本：n / 胜率 / 合计R / PF(价格收益) / ≥10R |
|---|---|---|
| 开发 | 1,460 / 24.32% / -909.02 / 0.542 / 9 | 1,460 / 18.56% / -962.56 / 0.450 / 5 |
| 后段 | 111 / 32.43% / -26.28 / 0.851 / 2 | 111 / 24.32% / -41.13 / 0.777 / 1 |

后段共有 59 笔达到 1R 启用条件，29 笔最终由入场价保护成交。19 笔原亏损减轻、10 笔恶化；减损合计 +15.78R，但盈利截断合计 -30.63R。一笔原本 +13.09R 的趋势被截断为价格保本成交，造成约 -14.57R 的逐笔差，并丢失一笔 ≥10R。后段原跟踪止损的净R合计为 +79.60R，保本后对应跟踪止损合计仅 +38.46R；保护少量亏损的收益不足以抵消失去的延续趋势。

价格保本也不是净保本：同样 0.2% 成本除以较小初始风险会放大为较大 `fee_R`。这解释了为何入场价出场仍可能显示净亏 R，但不改变更核心的尾部趋势被过早退出这一事实。

## 风险与下一步

- 这不是新的 V8 3m/5m 回测，也不证明成本门在过滤后的串行账户中可得正收益；交易并发、资金释放、滑点、资金费和真实盘口均未模拟。
- ETH 3m 后段仅 111 笔、3个月，费用门的微小正值未保留任何原 ≥10R，不能推广为胜率或收益改善。
- ETH 5m 成本门两段仍负，不能将 ETH 3m 的后段偶然正值推广到 Binance 5m。
- 1R 保本的固定入场和完整串行版本都已在前序实验变差；本轮没有搜索 0.8R、1.2R 或其他门槛。

如要研究新的低周期入场规则，需预注册并用独立未来样本验证；不能在已读取的费用桶、月份或赢家上继续选择阈值。

## 复现与附件

```bash
python3 -m pytest -q tests/evaluation/test_spike_eth_lowtf_cost_diagnostic.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 nice -n 10 \
  python3 -m yoyo.evaluation.spike_eth_lowtf_cost_diagnostic \
  --output experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3 --official
python3 scripts/md_to_html.py analysis/p1_spike_eth_lowtf_cost_diagnostic_20260914.md --out-dir analysis/html
```

- [基线逐笔账本](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/baseline_event_ledger.csv.gz)
- [成本门汇总](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/cost_budget_gate_summary.csv)
- [成本门月度汇总](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/cost_budget_monthly_summary.csv)
- [费用分桶](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/fee_r_bucket_summary.csv)
- [方向与退出原因](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/baseline_direction_exit_summary.csv)
- [ETH 3m 保本逐笔配对](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/eth3m_be_fixed_entry_pairs.csv.gz)
- [正式输入与产物收据](/Users/zhangzc/fable-trading/experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1/results/full_v3/manifest.json)
