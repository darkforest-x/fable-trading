# P2a 因果方向分类数据集验收

## 目标

构建独立于现有 `dense_cluster` 检测任务的 long / short / no_trade 分类数据集。模型输入
只能看到信号 bar 及之前 200 根 K 线；固定 TP5/SL2、h72 标签允许读取未来，但数据与
切分不得接触 judgment holdout。

## 复现命令

```bash
PYTHONPATH=. python3 -m src.detection.build_direction_dataset \
  --out datasets/ma206_direction_causal_v1
```

烟雾验证可使用：

```bash
PYTHONPATH=. python3 -m src.detection.build_direction_dataset \
  --out datasets/ma206_direction_causal_smoke \
  --limit-per-class-split 5
```

## 数据口径

- 候选：MA206 expanded long 与 short 数值规则的并集。
- 去重：同一币种相邻候选至少间隔 18 根 15m K 线，按时间最早先到先得；追加未来
  数据不能改写历史候选。
- 标签：固定 TP5/SL2、h72；仅 long 命中为 long，仅 short 命中为 short，其余为
  no_trade。
- 输入：200 根 K 线，`640×640` 方形画布，最后一根严格等于 signal bar，不包含未来
  价格；方形输入避免 Ultralytics 分类中心裁剪左右时间轴。
- 切分：全局时间顺序 80/20，并在 train/val 与 val/holdout 两处应用 18 小时 purge。
- 增强：本任务尚未训练；未来训练仍必须关闭 flip、mosaic、mixup 和 HSV。

## 结果

| split | long | short | no_trade | 合计 |
|---|---:|---:|---:|---:|
| train | 7,040 | 8,383 | 8,097 | 23,520 |
| val | 2,105 | 1,749 | 2,046 | 5,900 |
| 总计 | 9,145 | 10,132 | 10,143 | 29,420 |

- 币种数：272。
- train 时间：`2025-06-04 14:30` 至 `2026-03-23 10:30 UTC`。
- val 时间：`2026-03-24 11:00` 至 `2026-05-03 05:00 UTC`。
- train/val 实际间隔：24.5 小时，大于 18 小时 purge。
- manifest 行数与 PNG 数均为 29,420；缺图 0、重复 key 0、特征缺失行 0。
- 六个 split/class 抽样图均为 `640×640×3`；人工目视确认完整时间轴、最右 signal bar、
  K 线和 SMA/EMA20/60/120 正常渲染。
- manifest SHA-256：`ad174ad4dc6914dc87dc746fb7df1c7f9ff91fa7a5b27ae6476a3ccb29c9f1a2`。

## 因果性测试

测试把 signal bar 之后的全部 OHLC 改为极端值，再分别渲染原始与变异数据。两张图片
逐像素完全一致，证明未来行不进入模型输入。manifest 最晚信号为
`2026-05-03 05:00 UTC`，严格早于 `2026-05-04 00:00 UTC` holdout 的 18 小时 purge
边界。另一个回归测试模拟增量更新导致历史索引前移，物化器按 `signal_time` 重新定位，
输出仍与正确窗口逐像素一致。

## 风险与诚实声明

- long / short / no_trade 是固定 barrier 标签，不等于扣成本后可盈利；下一步必须用
  独立经济评估器报告方向准确率、覆盖率、每笔收益与 PF。
- 候选来自规则并集，不代表所有市场状态；模型只能在该候选分布内学习方向。
- 本数据集没有使用或评估 holdout，没有修改 TP/SL、成本、ACTIVE 或候选阈值。
- 数据集本体不入 git；代码、测试和本报告入 git，manifest 摘要用于本机复现验收。

## 验收

```text
22 passed
compileall passed
manifest_rows = png_files = 29420
missing_images = duplicate_keys = feature_nan_rows = 0
```

数据构建阶段通过。训练必须等待正在运行的 E2.1b 自然结束，避免复制训练或争抢 MPS。
