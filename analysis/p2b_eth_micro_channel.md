# ETH Micro 通道（1/2/3/5m）

**隔离**：不改 15m YOLO ACTIVE；仅 ETH_USDT_SWAP。

## 回测摘要（验收窗 @0.3%）

| bar | 候选 | val n | top净@0.2% | 验收笔数 | 验收PF | 验收净/资金 |
|---|---:|---:|---:|---:|---:|---:|
| 1m | 88 | 18 | +0.6200% | 26 | 1.25 | +0.20% |
| 2m | 264 | 25 | -0.6200% | 16 | 0.60 | -0.30% |
| 3m | 303 | 29 | -0.0110% | 30 | 0.54 | -0.76% |
| 5m | 915 | 156 | -0.1690% | 12 | 0.19 | -0.63% |

## 使用

```bash
# 回测
PYTHONPATH=. python3 scripts/eth_micro_backtest.py
# 实时监控(TG)
PYTHONPATH=. python3 scripts/eth_micro_monitor.py --loop --interval 60
```

前端：`#ethmicro` 或侧栏 **ETH Micro**。
