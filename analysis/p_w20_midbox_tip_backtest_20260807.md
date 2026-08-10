# w20 midbox tip 回测裁决 — 2026-08-07

> Owner 2026-08-07 明确批准：ATR 障碍 TP/SL + 全市场 tip 扫描 + matched control 置换 + **holdout**。

## 一句话

见下方 pre-holdout / holdout 表；检测器权重为 cold yolo11s 训在 `dense_owner_w20_midbox` 的 best.pt。

## 协议（固定）

- 权重：`/Users/zhangzc/fable-trading/analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt`
- Tip 窗：W=24 右对齐 tip；全历史算 MA 后切片渲染
- conf≥0.15；框右缘落在 tip/tip-1（TIP_EDGE=2）
- 同币 MIN_GAP=18 bar 去重
- 入场：信号 bar 的下一根 open
- 障碍：TP=5×ATR14 / SL=2×ATR14 / 72 bar 超时；同 bar 双触 → SL
- 成本：maker 往返 FORWARD_COST
- tip-stride=2（每隔一根 tip 扫描，诚实声明：非每一根，速度折中）
- matched control：同币 × UTC 月 × atr_pct 五分位随机入场，同障碍同成本
- 置换：UTC-week 整周 sign-flip，n=2000，双侧 p

tip_replay W=24 conf>=0.15 edge>=22; entry t+1 open; TP5.0/SL2.0/H72; cost=0.0006; MIN_GAP=18; matched control symbol×month×atr_q; week sign-flip perm n=2000

## Holdout 消耗登记

- **配置名**：`w20_midbox_tip_replay_W24_c0.15`
- **这是该配置第 1 次消耗 holdout**（Owner 2026-08-07 对话明确批准执行 holdout）
- holdout 起点：≥2026-05-04 UTC
- 未改 ACTIVE / 未下单 / 未改障碍默认

## Pre-holdout（训练侧外推窗）

| 指标 | 值 |
|------|-----|
| 区间 | 2026-03-01..2026-05-03 |
| 币种数 | 311 |
| 扫描 tip 数 | 687544 |
| 原始开火 | 19557 (28.445 /1k bars) |
| 入账成交 | **10713** |
| 胜率 | 0.3255 |
| 利润因子 PF | 1.006 |
| 毛均收益 | +6.6 bp |
| 净均收益（扣 maker RT） | **+0.6 bp** |
| 合计净收益（单位仓） | 0.62746 |
| 结局分布 | `{'sl': 6972, 'tp': 2800, 'timeout': 941}` |
| matched 对数 | 10713 |
| matched lift | **+4.9 bp** (se +2.6) |
| UTC-week 置换 p | **0.5202** |
| 成本 | 0.0006 |
| holdout | pre-holdout only |


## Holdout（第 1 次消耗）

| 指标 | 值 |
|------|-----|
| 区间 | 2026-05-04..2026-07-01 |
| 币种数 | 311 |
| 扫描 tip 数 | 747227 |
| 原始开火 | 21839 (29.227 /1k bars) |
| 入账成交 | **12141** |
| 胜率 | 0.2973 |
| 利润因子 PF | 0.836 |
| 毛均收益 | -12.6 bp |
| 净均收益（扣 maker RT） | **-18.6 bp** |
| 合计净收益（单位仓） | -22.57943 |
| 结局分布 | `{'sl': 8282, 'tp': 2818, 'timeout': 1041}` |
| matched 对数 | 12141 |
| matched lift | **+2.6 bp** (se +2.6) |
| UTC-week 置换 p | **0.7806** |
| 成本 | 0.0006 |
| holdout | w20_midbox tip-replay config holdout consumption #1; owner approved 2026-08-07 chat |



## 解读

- **Pre-holdout**：n=10713，净 +0.6 bp/笔，matched lift +4.9 bp，perm p=0.5202
- **Holdout**：n=12141，净 -18.6 bp/笔，matched lift +2.6 bp，perm p=0.7806 → **净亏**；检测器 tip 入场未证明成本后 edge

## 风险与诚实声明

1. tip-stride=2：不是每一根 tip 都扫，可能漏掉半个相位上的信号；密度与收益都可能有偏。
2. W=24 是训练窗 20–30 的中位折中；若最优推理窗不同，本结果不可直接外推。
3. matched control 用月×波动桶，不是完整共时市场组合回测。
4. holdout 已消耗 1 次；同配置再读 holdout 必须重新获批并记第 N 次。
5. 未 promote、未部署、未真下单。

## 复现

```bash
PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/backtest_w20_midbox_tip.py \
  --weights analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt \
  --start 2026-03-01 --end 2026-05-03 --tip-stride 2 --tag w20_tip_preholdout --device mps

PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/backtest_w20_midbox_tip.py \
  --weights analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt \
  --start 2026-05-04 --end 2026-07-01 --tip-stride 2 \
  --allow-holdout --holdout-n 1 --tag w20_tip_holdout --device mps
```

## 产物

- `analysis/output/w20_tip_preholdout.json` / `_trades.csv` / `_matched.csv`
- `analysis/output/w20_tip_holdout.json` / `_trades.csv` / `_matched.csv`
- 本报告 + HTML

生成于 {datetime.now(timezone.utc).isoformat()}
