# ETH 3m 专用做空模型 pilot v1 — 因果回放报告

日期：2026-07-29

## 一句话结论

**本轮不通过。** 严格时序 OOS 的 774 根盘口中，模型在 772 根上画了 tip 框，原始开火率
99.74%；18 根去重后仍有 43 笔，约
26.67 笔/有效日。3h 做空扣 20bp 后平均
-31.97bp，匹配随机做空为
-31.17bp，模型选择超额
-0.80bp。当前模型近似“持续开火”，不能进入判断层或 holdout。

## 复现命令

```bash
MPLCONFIGDIR=/private/tmp/mpl-eth3m-backtest PYTHONPATH=. .venv/bin/python \
  scripts/backtest_eth3m_short_pilot_v1.py --device mps --batch 32 --render-workers 6
PYTHONPATH=. .venv/bin/python scripts/validate_eth3m_short_pilot_backtest.py
PYTHONPATH=. .venv/bin/python scripts/build_eth3m_short_pilot_backtest_report.py
```

## 回放口径

- 模型：`runs/detect/runs/detect/eth3m_short_pilot_v1_mac_cold/weights/best.pt`
- 数据：ETH_USDT_SWAP 3m；holdout 起点 2026-05-04 00:00 UTC。
- 每次只给模型看决策 bar 及以前的 200 根；5,398 个回放窗口与 183 张 train/val 图片的
  K 线像素交集均为 0。
- 严格 OOS：最后一张 train/val 图片之后再经过完整 200 根像素隔离，2026-05-02 06:18
  至 2026-05-03 20:57 UTC，共 774 根；这是主结果。
- 间隙回放：训练日历之间、但与训练图片像素零重叠的 5,398 根；仅作辅助诊断。
- 固定 conf=0.30、tip/tip-1 门、18 根去重；决策 bar 收盘后识别，下一根 open 入场，
  60 根后 close 出场（3h），短收益 `1 - exit_close / entry_open`，扣固定 0.20% 往返成本。
- 随机对照严格匹配 ETH × 同一零重叠 run × ATR 五分位，每信号最多 3 个；不允许降级。

## 结果表

| 回放范围 | 有效 bars | 原始开火 | 开火率 | 去重信号 | 信号/日 | 模型净均值 | 随机净均值 | 匹配超额 | 净胜率 | 净 PF | 块置换 p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 严格 OOS | 774 | 772 | 99.74% | 43 | 26.67 | -31.97bp | -31.17bp | -0.80bp | 11.63% | 0.157 | 0.600 |
| 间隙回放 | 5398 | 5071 | 93.94% | 304 | 27.03 | -45.15bp | -40.71bp | -4.43bp | 15.46% | 0.075 | 0.773 |

### 静态验证与因果回放的对照

| 阶段 | P | R | mAP50 | mAP50-95 | 因果开火密度 |
|---|---:|---:|---:|---:|---:|
| 静态 val（16 个正例） | 0.729 | 0.675 | 0.735 | 0.443 | 未测 |
| 严格 OOS 逐 bar | N/A | N/A | N/A | N/A | **99.74%** |

静态 val 只证明模型能在同分布的稀疏图片上拟合框，不能证明连续盘口中会稀疏开火。
这不是通过提高 conf 就应立即修的阈值问题：严格 OOS 的原始框全部映射到当前 tip，且大量置信度很高；
在同一回放上扫阈值既违反阈值决策纪律，也会把数据集捷径伪装成修复。

## 解读

1. **检测层没有形成事件选择。** 严格 OOS 每 480 根/有效日中约 479 根开火，18 根去重只是在
   机械地每 54 分钟取一次，并没有把行情筛成少量事件。
2. **任何绝对收益都不能归给模型。** 严格 OOS 模型比同 run、同 ATR 桶随机做空还差
   0.80bp；间隙回放差
   4.43bp。块置换没有正向显著性。
3. **最可能的原因是训练目标的空间捷径和序列负样本缺失。** 76 张正例全部把框右缘固定在 tip，
   107 张负例又是离散的 owner-no 图片；静态 val 没有要求模型在一个正例周围的连续窗口中只开一次，
   因而模型学会了“ETH 图右侧放框”，而不是“只在启动时刻放框”。

## 本阶段不适用的项目指标

本轮只验收 YOLO 检测层，因此 AUC、top-decile、单特征基线和 LightGBM 置换检验均不适用；
它们必须等检测层能产生稀疏、可审计的事件池后再计算。方向性收益表已经按项目纪律加入匹配随机对照。

## 风险与诚实声明

1. 严格 OOS 只有 774 根、43 个去重信号和 2 个 run×UTC-day 统计块；它足以判定 99.74% 的
   开火密度失败，但不足以精确估计经济收益。
2. 3h 结果窗口互相重叠，逐信号 t 值偏乐观；报告以 run×UTC-day 符号置换 p 为更保守参考。
3. 间隙回放与训练日历交错，只能证明像素不重叠，不能替代严格时序 OOS。
4. 本轮未读取 2026-05-04 之后数据、未调 conf、未改成本/障碍、未 promote、未写 ACTIVE。

## 下一步选项（需 owner 决策）

1. **建议确认停止 v1，不进入判断层。** 判断层无法把一个 94%–100% 开火的检测器变成可信事件源。
2. **重做 v2 序列数据集。** 每个正事件只保留一个目标时点；增加同一事件前后连续窗口的
   “未形成 / 已经太晚”硬负例，并设置模糊区不训练；再加入预先封存时间块的背景负例。
3. **先决定检测层密度门。** 下一轮训练前由 owner 明确 raw fire/day、事件精度和来得及率上限；
   不在回放结果出来后倒推阈值。
4. **预留新的严格 OOS。** 先封存一整段未用于选图、标注或 early stopping 的连续 3m 时间块，
   v2 只允许一次验收；holdout 仍不动。

## 复核产物

- `analysis/output/eth3m_short_pilot_v1_backtest/summary.json`
- `analysis/output/eth3m_short_pilot_v1_backtest/validation.json`
- `analysis/output/eth3m_short_pilot_v1_backtest/scan_rows.csv`
- `analysis/output/eth3m_short_pilot_v1_backtest/signals.csv`
- `analysis/output/eth3m_short_pilot_v1_backtest/matched_controls.csv`
- `analysis/notebooks/eth3m_short_pilot_v1_backtest.ipynb`
