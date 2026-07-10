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
- 去重：同一币种相邻候选至少间隔 18 根 15m K 线。
- 标签：固定 TP5/SL2、h72；仅 long 命中为 long，仅 short 命中为 short，其余为
  no_trade。
- 输入：200 根 K 线，最后一根严格等于 signal bar，不包含未来价格。
- 切分：全局时间顺序 80/20，并在 train/val 与 val/holdout 两处应用 18 小时 purge。
- 增强：本任务尚未训练；未来训练仍必须关闭 flip、mosaic、mixup 和 HSV。

## 结果

| split | long | short | no_trade | 合计 |
|---|---:|---:|---:|---:|
| train | 6,725 | 8,187 | 7,218 | 22,130 |
| val | 2,028 | 1,688 | 1,833 | 5,549 |
| 总计 | 8,753 | 9,875 | 9,051 | 27,679 |

- 币种数：272。
- train 时间：`2025-06-04 15:00` 至 `2026-03-23 10:30 UTC`。
- val 时间：`2026-03-24 10:30` 至 `2026-05-03 05:00 UTC`。
- train/val 实际间隔：24 小时，大于 18 小时 purge。
- manifest 行数与 PNG 数均为 27,679；缺图 0、重复 key 0、特征缺失行 0。
- 六个 split/class 抽样图均为 `1280×742×3`，纯黑像素比例 0；人工目视确认 K 线和
  SMA/EMA20/60/120 正常渲染。

## 因果性测试

测试把 signal bar 之后的全部 OHLC 改为极端值，再分别渲染原始与变异数据。两张图片
逐像素完全一致，证明未来行不进入模型输入。manifest 最晚信号为
`2026-05-03 05:00 UTC`，严格早于 `2026-05-04 00:00 UTC` holdout 的 18 小时 purge
边界。

## 风险与诚实声明

- long / short / no_trade 是固定 barrier 标签，不等于扣成本后可盈利；下一步必须用
  独立经济评估器报告方向准确率、覆盖率、每笔收益与 PF。
- 候选来自规则并集，不代表所有市场状态；模型只能在该候选分布内学习方向。
- 本数据集没有使用或评估 holdout，没有修改 TP/SL、成本、ACTIVE 或候选阈值。
- 数据集本体不入 git；代码、测试和本报告入 git，manifest 摘要用于本机复现验收。

## 验收

```text
16 passed
compileall passed
manifest_rows = png_files = 27679
missing_images = duplicate_keys = feature_nan_rows = 0
```

数据构建阶段通过。训练必须等待正在运行的 E2.1b 自然结束，避免复制训练或争抢 MPS。
