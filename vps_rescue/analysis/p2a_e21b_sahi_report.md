# P2a E2.1b 固定 SAHI 全验证基准

## 结论

固定 SAHI 参数在 E2.1b 全部 1,255 张验证图上验收失败。Direct YOLO 精确复现既有
一致性结果 `665/1,297`；SAHI 只匹配 `625/1,297`，recall-like 从 `51.27%` 降至
`48.19%`。预测框从 1,629 增至 2,753（`+69.0%`），precision-like 从 `40.82%`
降至 `22.70%`，每张图延迟约为 direct 的 `11.27` 倍。

该指标是固定 conf 下的 IoU50 一对一诊断，不是官方 Ultralytics mAP。结果足以判定
**不晋升 SAHI、不接入检测主路径、不通过调整阈值或匹配定义救结果**。

## 固定配置

| 项目 | 值 |
|---|---|
| weights | E2.1b HSV0 `best.pt` |
| weights SHA-256 | `f9618b357a0bd8022c5dd1022482b785ac75899e317c7ed65bb061d26f4c6a65` |
| val images / GT boxes | 1,255 / 1,297 |
| confidence | 0.30 |
| direct imgsz / NMS IoU | 960 / 0.70 |
| match rule | IoU≥0.50，GT→prediction one-to-one greedy |
| slice | 640×371 |
| overlap | width=0.20 / height=0.20 |
| standard full-image prediction | enabled |
| SAHI postprocess | GREEDYNMM，IOS，threshold=0.50，class-aware |
| device | MPS |

切片尺寸和 overlap 在 E2.1b 结束前已预注册；本轮没有调参。SAHI 同时保留标准整图预测，
因此比较的是 direct 与“direct + sliced proposals + 固定 merge”的实际差异。

## 小样本预检

先按 seed `20260709` 跑 10 张链路预检：

| 模式 | GT | predictions | matched IoU50 | recall-like | precision-like |
|---|---:|---:|---:|---:|---:|
| Direct | 7 | 11 | 5 | 71.43% | 45.45% |
| SAHI | 7 | 28 | 4 | 57.14% | 14.29% |

两种模式均完成 10 条 checkpoint，坐标回映、标签读取与汇总链路通过。小样本只作运行
校验，没有用于改参数或提前终止全量实验。

## 全验证结果

| 模式 | predictions | matched IoU50 | recall-like | precision-like | pred / GT |
|---|---:|---:|---:|---:|---:|
| Direct YOLO | 1,629 | 665 | 51.27% | 40.82% | 1.2560 |
| SAHI fixed | 2,753 | 625 | 48.19% | 22.70% | 2.1226 |
| SAHI - Direct | +1,124 | -40 | -3.08pp | -18.12pp | +0.8666 |

Direct 的图数、GT、预测框和匹配数与
`analysis/output/consistency_e21b_hsv0_vs_gt.json` 完全一致，证明本基准没有发生口径漂移。
SAHI 新增大量碎框/边界框，却没有增加一对一命中，反而减少 40 个匹配。

## 延迟

| 模式 | 累计推理秒数 | 秒/图 | 相对 direct |
|---|---:|---:|---:|
| Direct YOLO | 41.01 | 0.0327 | 1.00× |
| SAHI fixed | 462.08 | 0.3682 | 11.27× |

计时包含每张图片的模型调用与 SAHI merge，不包含 checkpoint 写入和最终汇总。首张模型
预热会影响绝对值，但 1,255 张全量均值足以说明该方案的数量级成本。

## 解读

当前目标框并非单纯“小到整图完全看不见”。主要问题仍是 auto-label 对密集段边界、合并/
分裂的对象定义与模型 proposal 不一致。切片放大局部结构后产生更多 proposal，固定 merge
无法把它们恢复成 GT 的对象边界，因此框数上升而匹配下降。

这也解释了为什么 SAHI 不能解决交易收益问题：检测层只识别密集区域，不决定 long、
short 或 no_trade；而因果方向分类器本轮也因 `no_trade` 召回低、扣费后亏损被拒绝。

## 风险与诚实声明

- 本报告不是官方 mAP，不能与 E2.1b mAP50 `0.8505` 混用。
- 只验收一个预注册 SAHI 配方，不能证明所有切片/postprocess 组合都失败；但当前配方没有
  晋升价值，也没有理由在已用验证集上继续调参。
- 验证 GT 来自自动规则标签，GT 自身仍有人工审计发现的边界问题。
- 本轮未训练、未读取 judgment holdout，未改 conf、IoU、标签、增强、ACTIVE、成本、
  TP/SL、q90/q80 阈值或任何前向账本。

## 复现

```bash
PYTHONPATH=. uv run scripts/evaluate_sahi_detection.py \
  --dataset /Users/zhangzc/fable-trading/datasets/dense_15m_full \
  --weights /Users/zhangzc/fable-trading/runs/detect/runs/detect/dense_15m_full_s_e21b_hsv0/weights/best.pt \
  --limit 10

PYTHONPATH=. uv run scripts/evaluate_sahi_detection.py \
  --dataset /Users/zhangzc/fable-trading/datasets/dense_15m_full \
  --weights /Users/zhangzc/fable-trading/runs/detect/runs/detect/dense_15m_full_s_e21b_hsv0/weights/best.pt \
  --limit 0
```

验收：6 个 SAHI/一致性单元测试通过；direct 与 SAHI checkpoint 均为 1,255 行；完整
命令 exit 0。
