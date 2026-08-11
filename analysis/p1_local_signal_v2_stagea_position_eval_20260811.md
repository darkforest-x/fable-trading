# Local Signal V2 Stage A 训练与分位置诊断（2026-08-11）

## 直接结论

Stage A 离线预训练已正常完成，且通过推理前冻结的真实 K 线位置门：模型不再只识别真实内容
最右端，可作为严格因果 Stage B 的初始化权重。

这不代表“触发过多”已解决。固定 conf=0.05 时，easy-negative endpoint fire rate 仍为 26.54%，
整体 event precision 只有 15.23%；安静度必须在 Stage B 后通过新模型自己的 hard negatives 与
连续窗口回放解决。该权重继续标记 `production_eligible=false`，不得晋升或部署。

## 训练事实

| 项目 | 结果 |
|---|---:|
| run | `owner_lsv2_stagea_randomcrop_v1_cold` |
| 设备 | RTX 3060 12GB |
| 数据 | train 4,040 / val 716，正负 1:1 |
| 配方 | YOLO11s, imgsz 960, batch 8, seed 0 |
| 增强 | flip/mosaic/mixup/HSV 全部 0 |
| 配置轮数 / patience | 60 / 15 |
| 实际轮数 | 53，early stopping |
| 最佳轮次 | 38 |
| 总耗时 | 4,207.2s（约 70.1min） |
| 最终复验 P / R | 0.2376 / 0.4330 |
| 最终复验 mAP50 / mAP50-95 | 0.2332 / 0.1266 |

远端与本地取回产物逐一核对：

| 产物 | SHA-256 |
|---|---|
| `best.pt` | `c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a` |
| `last.pt` | `792080c2ddc592f5134477de41a429cd21c6305f890e56c5e9904277225b6417` |
| `results.csv` | `7ec7943737c3c8d142cc9fb413d9efd44427c83d9c7e8c34b8ccea240735af16` |
| `args.yaml` | `8c8d224acc73441ecc39872886f87b3bb4c2bcb3e663ce83fb758c2dee648600` |

## 冻结位置诊断

评估器与门在正式推理前提交。输入只含 Stage A 的 pre-holdout val：358 正例 + 358 easy
negatives；conf=0.05、NMS IoU=0.70、正例匹配 IoU≥0.50。

| 真实 K 线位置桶 | 事件数 | TP | Recall | 正例图 precision | 平均中心误差 |
|---|---:|---:|---:|---:|---:|
| left_mid | 72 | 61 | 84.72% | 19.37% | 0.20 bars |
| mid | 140 | 104 | 74.29% | 16.97% | 0.26 bars |
| mid_right | 106 | 80 | 75.47% | 18.56% | 0.32 bars |
| right | 40 | 28 | 70.00% | 20.29% | 0.28 bars |

冻结门：

| 门 | 实际 | 要求 | 结果 |
|---|---:|---:|---|
| 每桶最低 recall | 70.00% | ≥25% | PASS |
| 桶间 recall 最大差 | 14.72pp | ≤20pp | PASS |
| anchor X vs matched score Spearman | -0.134 | abs≤0.20 | PASS |

三个位置门全部通过。负相关幅度很小，且最右桶不是最强桶，因此没有“仍然只认最右边”的证据。
四桶匹配框的平均中心误差均小于 0.32 bars。

## 安静度与阈值诚实声明

| conf | Event P | Event R | F1 | Easy-negative fire rate | 裁决 |
|---:|---:|---:|---:|---:|---|
| 0.05（冻结位置门） | 15.23% | 76.26% | 25.40% | 26.54% | 位置可诊断，误触高 |
| 0.10（同 val 乐观 best-F1） | 22.66% | 52.79% | 31.71% | 13.97% | 仅诊断，不是验收点 |
| 0.35（旧 B2 conf） | 8.33% | 0.28% | 0.54% | 0.28% | 几乎静默且召回崩溃 |

旧 conf=0.35 不能沿用：它让 Stage A 几乎完全不报，并不是模型变安静。反过来，conf=0.05
虽然位置召回足够观察，却产生 1,519 个 false-positive boxes。阈值无法同时修复召回与误触，
后续必须依赖因果 Stage B 和 hard-negative 重训。

## 与前一方案同表对照

| 方案 | 真实内容位置 | 四桶门 | Easy-negative 安静度 | 生产资格 |
|---|---|---|---|---|
| B2 fixed-right | 约 93%/95%，100% 最右带 | FAIL（结构确定） | conf=.35 为 15.69% fire | false |
| blank-only v3 | 画布移动，内容仍贴右 | Owner 目视 FAIL | 未训练 | false |
| **Stage A real-crop** | **20%–85% 真实 K 线** | **PASS** | **仍需 hard negatives** | **false** |

该表只说明 Stage A 修好了位置表征。不同数据分布与权重的 easy-negative fire 不能作严格 A/B
收益归因，更不能外推连续市场触发次数。

## 模型与回测指标

本轮是 YOLO 表征训练，不产生 LightGBM val AUC、置换检验 p、top-decile 毛/净收益、胜率、
单特征基线或匹配随机入场对照，这些均为 **不适用**。没有读取价格 outcome，也没有把 716 个
平衡端点当成连续市场或订单回测。

## 复现命令

```bash
# 评估合同测试
PYTHONPATH=.:../yoyo-trading .venv/bin/pytest -q \
  tests/test_eval_local_signal_v2_stagea_position.py \
  tests/test_local_signal_v2_stagea.py \
  tests/test_local_signal_v2_stageb.py

# 正式推理（只读 pre-holdout Stage A val）
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/eval_local_signal_v2_stagea_position.py \
  --device mps --batch 16

# 已有 predictions 时只重算门，不重复推理
PYTHONPATH=.:../yoyo-trading .venv/bin/python \
  scripts/eval_local_signal_v2_stagea_position.py \
  --predictions analysis/output/p1_local_signal_v2_stagea_position_predictions_20260811.json
```

## 风险与诚实声明

- Stage A val 参与了 early stopping，不是独立模型验收集；best-F1 更是同 val 乐观选择。
- Stage A 图片按 owner 授权包含 decision 后真实 K，只能作为离线表征，不是盘口推理合同。
- easy negatives 是平衡抽样，不代表连续市场先验；26.54% 不能直接换算成 fires/day。
- 位置桶由冻结 seed 随机分配，支持位置诊断；每桶事件内容仍不完全相同，14.72pp 差异不能
  解释为纯因果位置效应。
- 本轮未读取 holdout，未改阈值、成本、障碍、新鲜度或 ACTIVE，未 promote、部署或下单。

## 下一步

固定 `best.pt` SHA-256 `c0e94f47…bf1a`，作为严格因果 Stage B 的唯一初始化权重；保持 Stage B
数据、seed、训练配方和增强不变，只改变初始化这一变量。Stage B 完成后从该新模型在
pre-holdout 连续窗口上的实际误报构建 hard-negative 集，再进行 P2 重训与密度回放。
