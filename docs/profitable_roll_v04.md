# 盈利滚仓 V0.4：Owner 选定最多两次加仓

2026-09-21，Owner 在阅读 V0.3 回测后明确：“那就2次加仓吧”。本版本选择已有 `two` 组作为当前研究使用方案；V0.3 源码、配置、账本与历史说明保持原样。

规则：**一笔初仓，最多两次成功加仓，共最多三笔成交腿**。加仓仍需已完成1h回踩突破、达到初始2R、共同止损提高，并满足利润保留、保证金、维持保证金与数量规格。条件不足则跳过；拒绝或跳过的机会不算成功加仓。加满两次后继续更新共同结构止损，不能因达到次数上限就停止保护。

当前入口固定最多两次，不接受其他次数：

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.profitable_roll_v04_cli \
  --prices /path/to/hourly.csv --marks /path/to/mark-hourly.csv \
  --tiers /path/to/risk-tiers.json --funding /path/to/funding.json \
  --entry '2026-08-19T18:00:00Z' --stop 0.1362 --requested-quantity 25000 \
  --tick 0.0001 --quantity-step 1 --min-quantity 1 \
  --max-order-quantity 270000 --capital 100 --leverage 40 \
  --output /tmp/roll-two-adds-case
```

省略 `--max-adds` 即采用2；显式指定只能为2。上述合约参数沿用原 WIF 研究示例，使用其他输入时需要对应的明确规格。结果写入 `strategy_profile=profitable_roll_v04_two_adds` 与 `max_adds=2`。输出目录必须不存在，CSV/JSON输入格式沿用实验README。

V0.4复用 `yoyo/evaluation/profitable_roll_v03.py` 的已测试会计引擎，仅选择两次组；0次与不限次数仍可通过原V0.3研究入口作对照。没有重新挑币、调参或把已有结果冒充新回测。

已有证据见 `analysis/p1_profitable_roll_v03_20260921.md`：后段两次版仍平均每笔净亏约3.08U；选择两次是Owner的管理规则决定，不是新的盈利证明。当前仍是离线研究入口，不构成实盘准入。

本次验证：复用既有12根合成行情，该路径不限版有3次加仓；新CLI省略次数参数只成交2次，随后仍更新止损，期末余额与原 `max_adds=2` 组一致。显式3次被拒绝。V0.3交付文件和两份run manifest声明的历史源码哈希全部保持相同。
