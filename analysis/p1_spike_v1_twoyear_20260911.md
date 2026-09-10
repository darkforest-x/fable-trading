# SPIKE 强劲爆发 V1：两年三所全市场回测 — 覆盖受限采集状态

## 结论

本配置**尚未**产生任何交易绩效结论、排行榜或交易账本。冻结的三所全市场合同在 Gate 两年 30 分钟历史上不可满足；Binance 与 OKX 正继续按 current-catalog 全目录收集，Gate 则按原生周期记录可得窗。评估器会拒绝把局部历史称为“all-market”。这次已消耗 owner 明确授权的该配置第 1 次 holdout 读取，用于验证固定 V1 和公共历史可得性；没有训练、调参、promote 或执行路径改动。

## 冻结合同与复现

- 评估窗：`[2024-09-10T00:00:00Z, 2026-09-10T00:00:00Z)`；这是采集时 UTC 为 `2026-09-10T17:17Z` 时最后完整的 UTC 日线边界。30m/1H/4H/1D 均从同一确认 30m 流按 UTC epoch 完整分组；不会混用 OKX `1D` 与 `1Dutc`。
- 为日线 V1 的 340 根 MA/SMMA 预热，源请求从 `2023-08-30T00:00:00Z` 开始。晚上市或断档段将是 warmup exclusion，而非凭空两年样本。
- 合约：只读 `yoyo/evaluation/pine/spike_burst_v1.pine` SHA256 `18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2`。display 文件 SHA `1fc6ec…` 的差异只在状态机之后添加显示消费者；V1 默认方向为**多头**。
- 命令：

```bash
python3 -m yoyo.evaluation.spike_v1_twoyear_allmarkets catalog
python3 -m yoyo.evaluation.spike_v1_twoyear_allmarkets fetch
python3 -m yoyo.evaluation.spike_v1_twoyear_allmarkets evaluate
```

最后一条只会在每个请求市场均完整、无缺口和无错误时运行；这是防止部分样本冒充全市场的门。

## 目录与实际覆盖

| venue | 当前目录中合格 USDT 永续 | 两年 30m 样本 | 结果 |
| --- | ---: | --- | --- |
| Binance USD-M | 658 | 2 个完整市场：0GUSDT 17,153 根/36 页；1000000BOBUSDT 22,159 根/15 页 | 全目录续跑中 |
| OKX SWAP | 460 | 2 个完整市场：0G-USDT-SWAP 16,923 根/178 页；1INCH-USDT-SWAP 53,136 根/178 页 | 全目录续跑中 |
| Gate USDT Futures | 563 | 0G_USDT 30m 被拒；原生 1H 8,576 根、4H 2,144 根可得但该 2025 上市标的自然不足两年 | 按周期覆盖收集，不能静默补成 30m |
| 合计 | 1,681 | 4 个 Binance/OKX 完整样本；Gate 1 个按周期审计样本 | 尚未达到全量评估 |

这是 current-catalog universe，不是 all-ever-listed：三个 2026-09-10/11 的目录快照不能找回已删除或历史退市合约。历史 listing 时间也只是当前目录字段，不能证明早期连续可交易性。

Gate REST 返回 HTTP 400：`INVALID_PARAM_VALUE`，正文为 “Candlestick too long ago. Maximum 10000 points recently are allowed”。直接按原生周期取数后，0G 的 1H/4H 可从其 2025 上市后连续读取，30m 仍被拒；日线返回 UTC 00 时钟，而该标的 listing 时钟不在日边界，故不自动重采样或挪动 timestamp。其官方 REST 文档也说明单次最多 2,000 点；历史下载说明列有 futures archive，但按其公开 URL 构造的 `futures_usdt/candlesticks_{30m,1h,4h,1d}` 对 BTC_USDT、ETH_USDT 和 0G_USDT 测试均为 404。原始响应、请求参数、HTTP 状态和页面 SHA 均保存于实验 `data/raw/gate/`、`data/market_receipts/gate/` 与 `data/gate_timeframe_receipts/`，没有静默换源。

## 执行与统计口径（已冻结，未运行）

V1 信号在收盘确认；诊断会另存 signal-close 风险参考，实际交易才会下一根开盘进入。保护线由信号收盘冻结；入场跳空穿越保护线时按该开盘价退出，后续单根中触及保护线按 stop-first 保守顺序处理。费用是既有 0.2% 往返名义成本。资金费、各所费率档、冲击和容量没有完整历史，均为未建模，不能当作零。

本应交付的全交易 ledger（时间、特征、MAE/MFE、费用、net R、退出原因）、按 venue/timeframe/month/year/regime 的 PF、等风险与容量组合、同币同周同波动桶随机对照、bootstrap 与 feature bins 均为**不适用**：没有完整母体，生成其中任何一个会制造局部市场结果。AUC 也不适用，因为 V1 是固定规则、非分类器。

## 风险与诚实声明

Binance/OKX 两个新上市样本只验证了当前采集器的路径，不能推断历史质量、V1 收益或跨所稳定性。Gate 缺失不是零成交量、失败策略或可由 Binance/OKX 替代的证据。未完成全市场数据，不能据此修改 V1 参数、通知、模型、ACTIVE 或真实仓位。

## 下一步选项

1. Owner 指定一个可审计、许可明确且覆盖 Gate 两年 30m 的历史来源；冻结其原始文件/许可证/市场目录后从头跑同一合约。
2. Owner 明确把研究问题收窄为“Binance+OKX current-catalog covered universe”，作为**新配置**重新登记并单独消耗 holdout；它不能继承本三所全市场结论。
