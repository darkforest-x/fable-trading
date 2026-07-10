# P2b 判断层统一 SMA/EMA 20/60/120

## 结论

2026-07-10 owner 明确推翻 07-09 的旧裁决，要求检测层、判断层及未来运行路径全部统一为
SMA20/60/120 + EMA20/60/120。迁移已完成，旧 8-55 模型与日志仅保留为历史审计，不再被
默认配置、前向扫描或看板加载。

本次是架构统一，不是盈利验收通过。全量 SWAP 池复算后，MA206 在已重复使用的 val
上有统计区分度，但经济性仍弱：0.2% 往返成本下 LightGBM top-decile 净收益为负；
SWAP maker 组合 PF 仅 1.072，低于 1.3 验收线。它不是“完全没收益”，只是尚未形成
足以覆盖保守成本并通过组合验收的稳定优势。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/swap_replication.py
PYTHONPATH=. python3 scripts/freeze_model.py --date 20260710
PYTHONPATH=. python3 -m src.judgment.train \
  --data data/ma206/swap_tp5_sl2_ma206.csv \
  --tag p2b_ma206_mainline_20260710
PYTHONPATH=. python3 -m src.backtest.maker_val_sim \
  --data data/ma206/swap_tp5_sl2_ma206.csv \
  --out analysis/output/p3_ma206_maker_val_sim.json
PYTHONPATH=. python3 -m src.backtest.run \
  --data data/ma206/swap_tp5_sl2_ma206.csv \
  --tag p3_ma206_preholdout
PYTHONPATH=. python3 scripts/forward_track.py
PYTHONPATH=. python3 scripts/forward_track_h1_shadow.py
```

以上命令均未传 `--eval-holdout`。但迁移后的首次浏览器验收暴露出旧看板会绕过该开关，
详见“风险与诚实声明”。

## 数据与冻结工件

| 项目 | 数值 |
|---|---:|
| 扫描 SWAP 序列 | 358 |
| 产生候选的币种 | 312 |
| 候选数 | 19,666 |
| 正类率（全数据） | 25.99% |
| train | 12,032 |
| val | 3,030 |
| val 时间 | 2026-03-21 20:00 至 2026-05-03 05:30 UTC |
| 特征数 | 28 |
| 数据 SHA256 | `8df081a1374c0edb1ef8a869cc4825830ecb2f07fd00209306c44dcc272040d1` |
| 模型 | `models/frozen_tp5_sl2_swap_ma206_20260710.txt` |
| best iteration | 32 |
| val q90 阈值 | 0.3409333202 |

旧特征名 `close_vs_ema55` / `close_vs_ema200` 已移除；新数据重新计算真实的
`close_vs_ema60` / `close_vs_ema120`，不是只改列名。

## 结果

| 口径 | AUC | p | top 毛收益/笔 | 净收益/笔 | 胜率 | PF | 笔数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| LightGBM top-decile，0.2% 成本 | 0.5702 | 0.001 | +0.079% | -0.121% | 32.67% | — | 303 |
| 事件驱动组合，0.3% 成本 | — | — | — | -0.205% | 35.11% | 0.636 | 282 |
| 组合回测，SWAP maker 0.06% | — | — | — | +0.030% | 36.12% | 1.072 | 227 |
| maker + 1h EMA120 顺势 | — | — | — | +0.062% | 37.35% | 1.154 | 166 |
| 单特征 `ma_spread_pct`，0.2% 成本 | 0.5085 | — | +0.234% | +0.034% | 34.32% | — | 303 |

旧的小样本对比曾出现“20/60/120 AUC 更高、扣费收益更弱”，因此 07-09 暂时保留旧
均线。全量结果修正了“AUC 更高”的表述：当前 LightGBM AUC 只有 0.5702，虽然 p=0.001，
但 0.2% 成本口径每笔 -0.121%，且经济排序弱于 `ma_spread_pct` 单特征基线。与此同时，
更贴近合约 maker 的 0.06% 组合口径仍为正。旧对比直接复用为 8-55 选出的 TP5/SL2、
h72 和评分阈值，因此既不能证明 MA206 天生无收益，也不能证明它已可交易。

成本敏感性是当前最直接的答案：保守 0.3% 往返成本下验证集组合 PF 只有 0.636，明显
亏损；理想化 maker 0.06% 下才出现 PF 1.072 的微弱正值。因此目前不能说“20/60/120
没收益”，只能说它的毛优势太薄，是否能在真实成交中留下正收益尚未证明。

## 运行迁移

- `src/judgment/candidates.py` 现在只计算六线，连续密集至少 5 根；
- `src/judgment/features.py` 只暴露 EMA60/120 锚点特征；
- 默认冻结配置改为 `tp5_sl2_swap_ma206`，`models/ACTIVE` 已切换；
- 主线与 H1 影子使用独立新账本，起点为 2026-07-10 10:30 UTC；
- `/api/chart` 和信号页只绘制 SMA/EMA 20/60/120；
- 旧 `candidates_v206.py` 仅为导入兼容层，不再维护第二套逻辑。

冻结模型更新后重新执行前向冒烟：358 个 SWAP 序列、21,086 个历史候选；
`2026-07-10 10:30 UTC` 后阈值信号 0，主线与 H1 影子账本均为 0/100。空样本不能
代表策略通过或失败。

## 风险与诚实声明

- **这是 MA206 配置第 1 次消耗 holdout，且未经 owner 批准**：2026-07-10 首次浏览器
  验收时，旧看板对全数据评分并短暂生成 holdout 回测（373 笔、PF 0.638@0.3%）。这是
  实现缺陷导致的意外读取，结果已隔离作废，不用于任何选择、结论或参数调整；
- 修复后评分器默认在 `2026-05-04` 前停止，缓存身份强制记录
  `score_scope=pre_holdout_only`，旧现货 8-55 入口关闭；API 实测最大入场时间为
  `2026-05-03 22:15 UTC`；
- val 已被多轮实验使用，所有 val 数字仅用于迁移校验，不可宣称未来收益；
- owner 要求统一均线，因此即使 MA206 当前经济指标弱于旧 8-55，也执行架构替换；
- 新前向账本当前 0/100，交易系统仍未达到可实盘验收状态；
- 未改成本、TP/SL、horizon、score quantile 或实盘开关；除上述意外读取外未再次动用 holdout。

## 下一步

保持参数冻结，持续积累 `data/forward_log_ma206.csv` 的 maker-filled closed 样本；达到
100 笔后再按净收益、PF≥1.3、maxDD≤20% 做终审。终审前不得用前向结果调阈值。
