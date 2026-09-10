# SPIKE 强劲爆发 V1：两年三所全市场回测 — 覆盖受限账本的可执行口径修正

## 结论

2026-09-11（北京时间）完成了覆盖受限 V1 账本的经济口径重建。它读取的是本轮运行开始时已落盘、带收据的 OHLC CSV；本阶段没有发起行情抓取、训练、参数搜索、通知、模型切换或交易操作。当前覆盖仍不足以构成“三所全市场”或收益有效性的结论：固定分母 **6,724** 个 current-catalog × timeframe 单元中，最新一次本地冻结输入重建后有 **4,464** 个实际 evaluated，生成 **6,253** 个信号行；其中 **6,170** 行已实现退出，**83** 行在观察窗右端 censored。

本次消耗的是 owner 已授权的该配置第 **1** 次 holdout 读取。它只修正已授权配置的账本因果与统计，不用这些数值选择 V1 参数，也不主张任何盈利、PF、胜率或账户回撤结论。

## 本次修正

原账本在下一根开盘入场，却以信号收盘参考风险计算 R。现在每行在可执行时钟上冻结：

```text
risk_fraction_at_entry = (entry_price - initial_stop) / entry_price
net_r = net_return / risk_fraction_at_entry
```

`initial_stop` 是信号时刻冻结的保护线；之后的保护线 ratchet 不会改写入场风险。若下一根开盘不高于该保护线，风险距离不为正，行仍保留而 `net_r` 为空。例：信号收盘 100、初始保护线 90、下一根开盘 110，风险为 `20 / 110`，不是信号时刻的参考数值。

`censored=true` 的行不再进入已实现交易数、胜率、PF、expectancy 或净收益。汇总将已实现行按 `exit_time`、`entry_time`、`event_id` 稳定排序，并以零基线累加等权事件收益。输出字段名为 `event_sequence_drawdown`，因为它没有资本分配、杠杆或重叠仓位模型，不能称为账户最大回撤。

此外，空的冻结 Gate CSV 现在被识别为合法的空 UTC 时间轴；非空但无效、倒序或重复时钟仍 fail closed。此前这类空源触发 `Index.tz` 异常并中断覆盖重建。

## 冻结合同与复现

- 评估窗：`[2024-09-10T00:00:00Z, 2026-09-10T00:00:00Z)`；预热始于 `2023-08-30T00:00:00Z`。
- V1 是原始多头 Pine：`yoyo/evaluation/pine/spike_burst_v1.pine` SHA256 `18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2`。
- replay 源 SHA256 相同；覆盖 receipt、进度 manifest 和报告 manifest 都钉住 Pine SHA、replay 源 SHA 及方法版本 `covered-v2-next-open-risk-realized-event-sequence`。
- 费用仍为既有 0.2% 往返名义成本；资金费、实际费率档、冲击、流动性、杠杆和仓位重叠均未建模。

```bash
python3 -m pytest -q tests/test_spike_v1_twoyear_allmarkets.py tests/test_spike_v1_twoyear_accounting.py
python3 -m yoyo.evaluation.spike_v1_twoyear_allmarkets evaluate-covered
python3 -m yoyo.evaluation.spike_v1_twoyear_allmarkets report-covered
python3 -m yoyo.evaluation.spike_v1_coverage_diagnostics
python3 scripts/md_to_html.py analysis/p1_spike_v1_twoyear_20260911.md --out-dir analysis/html
```

本轮专属测试为 13 passed。它覆盖完整 UTC 聚合、未确认 OKX bar、可执行 next-open 风险、非正风险 R 为空、censored 排除、稳定事件序列回撤、空/无效冻结时间轴、缺 OHLCV schema 的 fail-closed 分类，以及 Binance/OKX/Gate 对 gapped/error 终态 receipt 不重抓。

## 当前覆盖快照

| 状态 | 单元数 |
| --- | ---: |
| evaluated | 4,464 |
| source_error | 894 |
| source_gapped | 867 |
| warmup_insufficient | 499 |
| 固定分母 | 6,724 |

逐笔账本位于 [covered_trade_ledger.csv.gz](../experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz)，可搜索浏览页位于 [covered_trade_drilldown.html](../experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_drilldown.html)。这些文件只描述已覆盖单元，不能替代完整市场、匹配随机对照、资金组合或样本外评估。

AUC、置换检验、top-decile 净收益、匹配随机交易对照在这一固定规则且覆盖未完成的账本修复阶段不适用；因此没有编造这些指标或留作隐性成功信号。

## 三周期已覆盖子集的描述性统计

为避免既有账本中的旧 1D 行混进当前协议，诊断器只读取 **30m / 1H / 4H**。它按 `exit_time`、`entry_time`、`event_id` 排序，先以零作为等权事件序列基线，再计算 `event_sequence_drawdown`；这不是账户净值或账户回撤。下面数值来自同一冻结 receipt，明细保留在 `results/coverage_diagnostics_by_timeframe.csv`、`..._by_entry_year.csv` 与 `..._by_exit_reason.csv`。

| 周期 | 信号行 | 已实现 / censored | 已实现正 / 非正 | 已实现正收益率 | 已实现等权净收益和 | 已实现净 R 和 | 已实现 PF | 事件序列回撤 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 30m | 3,594 | 3,579 / 15 | 1,038 / 2,541 | 29.00% | -10.3299 | -41.3873 | 0.9323 | -28.4789 |
| 1H | 2,039 | 2,024 / 15 | 537 / 1,487 | 26.53% | +34.4558 | +943.0324 | 1.2790 | -35.6811 |
| 4H | 552 | 513 / 39 | 146 / 367 | 28.46% | -15.4274 | -46.9460 | 0.7497 | -26.9365 |
| 合计 | 6,185 | 6,116 / 69 | 1,721 / 4,395 | — | — | — | — | — |

这只是当前可获得单元的等权描述，正负值均不能视作策略的成功或失败结论。`net_r` 使用本轮修复后的 next-open 风险分母，仍是单笔标准化账本字段，不是账户收益；事件序列回撤也没有资本分配或重叠仓位模型。按入场年份、交易所和周期拆分的完整行在 CSV 中保留，避免将不同时间段混成一个成绩单。

**获利记录与失败原因。** 这 6,116 条已实现行的 exit label 全部是 `protective_stop`：其中 1,721 条最终 `net_return > 0`，4,395 条非正；因此该 label 不能被误读成“每一条都是失败”，也不能从中单独识别或归因“成功大趋势”。同一已覆盖子集的所有 protective-stop 行等权净收益和为 +8.6985，中位 MFE 为 +5.85%，中位 MAE 为 -4.02%。这些是路径描述，不是完整市场、匹配对照或策略 edge；不能据此改变 V1 的风险线或阈值。

逐周期逐笔 source-event id、entry/exit、censored 与 v2 字段仍在 [covered_trade_ledger.csv.gz](../experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz)。目前 monitor 仅已导入其中 1,019 条旧已取回放卡；尚有 **5,166** 条 30m / 1H / 4H 账本信号未接入 UI。它们必须从同一不可变 snapshot 以 signal-only import 后再联结，不能只导入获利记录，1D 也不属于当前 V1 三周期 UI 或通知合约。 信号 API 的稳定 cursor 已就绪：页面一次只保留最多 2,000 条，用户可继续加载更早的 raw 历史，定时刷新也不会丢失已加载页。它解决浏览路径，不代表剩余 5,166 条已经导入或通过经济审计。

## 交易所、来源跨度与逐笔极值

下表读取 `coverage_descriptive_by_venue_timeframe.csv` 的已实现独立事件描述；成本沿用固定的 0.2% 往返名义成本。`净收益和`、PF 和胜率均只在该交易所×周期的已覆盖子集内计算，censored 不进入分母，也没有匹配随机对照或账户模型。

| 交易所 | 周期 | 已实现 / censored | 已实现正收益率 | 已实现净收益和 | PF |
| --- | --- | ---: | ---: | ---: | ---: |
| Binance | 30m | 2,365 / 8 | 29.01% | -2.6936 | 0.9748 |
| Binance | 1H | 1,327 / 6 | 25.40% | +11.0786 | 1.1288 |
| Binance | 4H | 296 / 15 | 28.04% | -5.8059 | 0.8418 |
| Gate | 30m | 34 / 1 | 17.65% | -0.7388 | 0.5464 |
| Gate | 1H | 50 / 0 | 32.00% | -0.4040 | 0.8662 |
| Gate | 4H | 84 / 5 | 32.14% | -5.5231 | 0.4759 |
| OKX | 30m | 1,180 / 6 | 29.32% | -6.8975 | 0.8437 |
| OKX | 1H | 647 / 9 | 28.44% | +23.7811 | 1.6902 |
| OKX | 4H | 133 / 19 | 27.07% | -4.0984 | 0.7154 |

按来源是否连续覆盖到 frozen start 分层，1H 的正数集中在 `partial_after_frozen_start`：full 1H 为 936 条已实现、-20.6536、PF 0.6173；partial 1H 为 1,088 条、+55.1094、PF 1.7925。30m full / partial 分别为 -1.2770 / -9.0529，4H 为 -11.4949 / -3.9325。这个不稳定的跨度差异正是不能从 partial 覆盖外推“edge”的原因。

逐笔极值只用于核对账本路径，不能解释为策略的“成功大趋势”：最大两条为 2026-04-07 的 RAVE 1H（OKX +32.9379，Binance +29.9506），都属于 partial 来源窗口；最大负行是 Binance MOVR 4H -0.6763，属于 full 来源窗口。全部已实现行的 lifecycle label 均为 `protective_stop`，因此该标签只说明退出机制路径，不能单独归因为赢家或失败原因。完整 top-winner / top-loss 列表和 source scope 都保留在同一结果目录的 `coverage_descriptive_*.csv`，没有挑选它们作为前端或通知输入。

## 本次重建的来源边界

`954e54a` 后，2026-09-10T22:38:36Z 的一次已有冻结输入重建完成；随后 `report-covered` 在 22:38:46Z 生成同一方法版本的 drilldown。此前一个标为 complete 但没有 OHLCV schema 的 `US100-USDT-SWAP` 源会使整轮聚合 `KeyError`；现在其四个周期均为 `source_error`，coverage detail 含原 receipt 路径、CSV 路径和缺失列名。该分类没有补写、重抓或伪造任何 K 线。

Binance/OKX 的 `gapped/error` 与 Gate 的 `complete/partial/gapped/error` receipt 现在都被视为该固定窗口的终态，循环不会把同一已知失败无限重抓。新增覆盖来自此轮之前已落盘且本轮 Gate worker 已完成的 receipt；它不代表全市场连续两年历史，也不替代缺口的来源。

## 风险与诚实声明

当前目录是 2026-09-10/11 的 current catalog，不是历史退市合约普查。Gate 的历史窗口和个别来源缺口仍被单元状态明确保留，不能用其他交易所替代。即使某一覆盖文件的已实现汇总为正或负，也会受存活偏差、未覆盖单元、未建模交易成本及非账户资金路径影响，不能据此修改 V1、通知、ACTIVE、模型或真实仓位。

监控台现在有全部 6,185 条三周期 `replay/raw` 信号：6,082 条为 `covered_linked_realized_unverified`，69 条为 `covered_linked_censored_unverified`，34 条因缺同源冻结 OHLC 保持 `unverified/ohlc_missing` 且不显示 outcome 或借图。68 条 1D 账本行没有导入当前三周期 UI/通知协议。原先指向可变汇总文件的 1,019 条链接已先降级为 stale evidence，审计保留而旧 outcome 不显示；当前可联结行保存双方 id、四元组、输入 SHA 和内容寻址副本 SHA。它仍不是独立经济审计或策略验证；详情见 [p1_spike_v1_replay_ledger_link_20260911.md](p1_spike_v1_replay_ledger_link_20260911.md)。

本轮只解决会污染已覆盖账本的可执行时钟与已实现统计问题。全市场覆盖、独立经济账本验收、匹配随机对照和任何策略有效性判断仍未完成。
