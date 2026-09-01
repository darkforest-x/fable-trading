# P1：YOLO 提案 + 因果语义门配对验证（2026-09-02）

## 结论

模型没有白训练，但它不再有资格单独作最终裁决。本轮把 1280 full40 YOLO 保留为**位置与方向提案层**，在其后增加一层完全确定的数值语义复核：六均线是否仍密集、K 线是否仍贴近均线、核心与启动方向是否一致，以及检测窗口里已经可见的 post 确认是否达标。

在同一批冻结的 pre-holdout 图片、同一权重、同一原始预测上，语义门把当前结构后处理放行的空标签误报框从 **35 降到 10（-71.43%）**；正例事件至少命中一次从 **142/155 降到 141/155**，即保留原有事件命中的 **99.30%**。预注册的全部成功条件通过。

这证明“两层处理”值得保留：YOLO 找候选，原始训练语义负责否决明显错误候选。但它**尚未证明 4h 可用**，也没有授权把门接入 ACTIVE、forward 或部署。

![配对 A/B 总览](../experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/semantic_gate/paired_ab_overview.png)

## 问题与单变量

此前 4h 全币种图里出现了“框内 K 线离均线很远、六均线也不密集”的预测。直接原因不是“图形检测天然不能跨周期”，而是当前后处理只把框横坐标映射成 `core4/5 + post2–9`，没有重新验证正例训练时的数值语义。

本轮唯一处理变量是：

- 对照：`YOLO conf=0.25 + NMS=0.70 + x 坐标映射 core4/5 + post2–9`；
- 处理：在完全相同的对照框上再加一项布尔语义门；
- 不变：图片、权重、原始预测、类别、置信度、IoU=0.50、标签和事件身份。

语义门没有新拟合阈值，逐项复用 10,000 个训练正例生成合同：

| 语义项 | 冻结门槛 |
|---|---:|
| core 内六均线总包络 | ≤ 1.5 ATR |
| core 末端六均线价差 | ≤ 1.1 ATR |
| core 最大实体 | ≤ 1.2 ATR |
| core 方向进展 | [-0.6, 1.3] ATR |
| post1 / post2 方向进展 | ≥ 0 / ≥ 1.0 ATR |
| 均线方向斜率 | ≥ 0.03 ATR |
| 至少一次 close 到任一 MA 距离 | ≤ 1.0 ATR |
| 所有 close 到 MA 包络最大距离 | ≤ 1.9 ATR |
| 所有实体到 MA 包络最大距离 | ≤ 1.5 ATR |
| post3 / post5 方向进展 | 仅在已经可见时要求 ≥ 1.25 / ≥ 1.75 ATR |

ATR 固定取 `core_end + 2`，因为结构门本来就要求至少 post2。post2 图不读取 post3/post5；post3/4 图不读取 post5。原始框的纵向 MA 覆盖率只记录，不参与 v1 放行，避免看完结果后发明新的 y 阈值。

## 数据、切分与身份

| 项目 | 数值 |
|---|---:|
| 时间范围 | 2025-12-02 22:00 ～ 2026-05-03 20:30 UTC |
| 冻结 val 图片 | 4,800 |
| 正例图片 / 事件 | 1,200 / 155 |
| 空标签负例图片 / 事件 | 3,600 / 465 |
| hard / easy 负例图片 | 2,400 / 1,200 |
| 原始模型 | Grade-A 8,000 正 + 24,000 负，full40，native 1280 |
| 权重 SHA256 | `862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838` |
| 数据 manifest SHA256 | `22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2` |
| 实际读取的行情前缀文件 | 133 |
| `>=2026-05-04` 被物化的 OHLCV 行 | **0** |
| 源图逐像素复现抽检 | 133/133 通过 |

每个行情文件均由 `read_preholdout_prefix` 在 holdout 边界前停止；只允许检查首个边界行的时间戳，不读取该行 OHLCV。验证器又独立复算了 4,800 行配对账本、1,161 个结构框、条件未来可见性和全部产物哈希。

## 主要结果

| 指标 | 当前结构门 | 加因果语义门 | 变化 |
|---|---:|---:|---:|
| 正例事件至少命中一次 | 142 / 155（91.61%） | 141 / 155（90.97%） | -1 事件，-0.65pp |
| 原命中事件保留率 | — | **141 / 142（99.30%）** | 通过 ≥90% 门 |
| 正例图片真命中 | 1,012 / 1,200（84.33%） | 981 / 1,200（81.75%） | -31 图片，-2.58pp |
| 空标签开火图片 | 35 / 3,600（0.972%） | 10 / 3,600（0.278%） | **-71.43%** |
| 空标签框 / 千图 | 9.72 | 2.78 | **-71.43%** |
| 开火负例事件 | 18 / 465（3.87%） | 7 / 465（1.51%） | -61.11% |
| 所有结构框 | 1,161 | 1,071 | -90（-7.75%） |

空标签图片的严格配对结果是：25 张只在对照开火、0 张只在处理开火、10 张两边都开火，精确双侧符号检验 `p=5.96e-8`。负例事件层是 11 个只在对照开火、0 个只在处理开火、7 个两边都开火，`p=0.0009766`。

正例事件只有 1 个“对照命中、语义门未命中”，其余 141 个共同命中。由于只有一个不一致事件，该侧的符号检验 `p=1.0`；这里的合理读法是报告**效应大小与预注册容忍度**，不能把“不显著”误写成无损。

## 为什么被挡掉

90 个结构框被拒绝，失败项可重叠：

| 失败项 | 框数 |
|---|---:|
| post5 | 27 |
| post2 | 24 |
| 最大实体过大 | 19 |
| post3 | 15 |
| core 方向进展 | 14 |
| 六均线总包络过宽 | 7 |
| 均线斜率方向 | 5 |
| core 末端均线价差过宽 | 4 |
| post1 | 2 |
| 至少一次贴线 / close 包络距离 / 实体包络距离 | 各 1 |

这说明 15m 同生成器验证集上的主要误报不是“均线都很散”，而是启动确认或核心形态不符合训练定义；密集度与贴线门仍能明确拦住对应坏例。4h 截图里那种明显稀疏形态是否会被同一冻结门系统性清掉，必须另做一次不调参的 4h 锁定评估，不能从本表外推。

框纵向诊断也没有被藏起来：1,161 个结构框覆盖六均线包络的均值为 99.91%、中位数 100%，但最差为 0%。同分布 val 大多数 y 框没问题，因此本轮不增加 y 阈值是合理的；它仍是未来错误定位审计项。

## 正例代价与唯一净损失事件

图片层损失 31 个真命中，分布在 11 个事件；由于同一事件有 post2–9 多个变体，10 个事件仍有其他变体通过。唯一完全丢失的是：

- `TA_USDT_SWAP`，LONG，事件 `d6f11213a72dd1d98426`；
- 其 post2–7 六个已命中变体全部被挡；
- 原因均为预测框横向映射出的核心未达到 post2/post3 进展门，而不是置信度不足。

这暴露的是**定位映射成本**：IoU≥0.50 仍可能把框边缘映射到与 Gold 不同的离散核心。若要救这一类事件，应把“框附近 ±1 bar 的 4/5 核心搜索”作为下一项单变量预注册实验，不能在本轮结果后悄悄放宽语义阈值。

完整拒绝样本原图见：

- [被消掉的负例、损失真命中与额外框画廊](../experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/semantic_gate/rejected_examples/index.html)
- [独立验证回执](../experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/verification.json)

## 零假设、统计口径与不适用项

这是目标检测语义过滤实验，没有入场、出场、TP/SL、成本或收益序列。因此 val AUC、top-decile 毛/净收益、胜率、收益置换检验、单特征收益基线和“同币×同时间块×同波动桶随机入场”均不适用；不编造这些指标。

同等严格的零假设对照是：保持每张图片、原始框、预测类别、置信度和 IoU 完全不变，只把数值语义计算里的 LONG/SHORT 翻转。实际方向仍命中 141/155 个事件，翻转方向为 0/155，配对精确 `p=7.17e-43`。这证明语义门不是只靠方向无关的“均线窄”条件机械放行。

## 风险与诚实声明

- 本轮只证明 frozen 15m chronological val 上的语义过滤有效；**未读取或评分 4h 半月 holdout**。
- 该模型使用 post2–9，是完成态研究检测器，不是 tip / tip-1 / tip-2 新鲜盘口模型，不能进入实盘路径。
- 语义门减少 71.43% 的空标签框，但仍留下 10 个；它不是“误报清零”。
- 15m val 正例和负例来自同一生成体系，自家 val 不能替代新鲜 Gold 或前向 100 笔确认。
- 本轮没有调 conf/NMS、没有阈值网格、没有训练、没有改标签、没有 promote、没有部署、没有改 forward 或订单状态。
- 正式 A/B 前第一次 CLI 启动因仓库根路径未加入 `sys.path` 而在 import 阶段退出，未生成结果；入口修复、脚本哈希和预注册绑定更新并提交后才运行上述唯一正式结果。

## 下一步选项

1. **建议**：Owner 明确批准后，把现在已经锁死的 v1 门一次性应用到现有 4h 半月冻结快照，输出同一事件 before/after 全图；必须登记该配置的下一次 holdout 消耗，禁止再改阈值。
2. 在 pre-holdout 上单独测试“预测框附近 ±1 bar 离散核心救援”，目标是救回 TA 事件类型；这是一项新变量，不能与 4h 评估打包。
3. 把仍通过的 10 个空标签框作为 hard-negative 审核候选；当前 P0/P1 纪律下不自动开新训练，训练需 Owner 另行授权。

当前不建议直接把 v1 语义门接入生产：它通过的是完成态 pre-holdout 验证，不是新鲜 tip Gold。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading

# 1. 单元、层间与已落盘产物复算
.venv/bin/python -m pytest -q \
  tests/test_ma_launch_yolo_semantic_gate.py \
  tests/boundaries/test_layer_imports.py

# 2. 冻结 1280 权重在 4,800 张 chronological val 上只推理一次
.venv/bin/python scripts/evaluate_15m_ma_launch_owner_grade_a8000_val.py \
  --weights analysis/output/ma_launch_owner_grade_a8000_neg24000_v1/ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt \
  --weights-sha256 862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838 \
  --dataset datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1 \
  --output experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/raw/frozen_val_evaluation.json \
  --predictions-output experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/raw/frozen_val_predictions.jsonl \
  --experiment-id exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1 \
  --generator-commit cd82127e6216b972009b79ac7bdd6a3a0b4bdd97 \
  --device mps --batch 8 --imgsz 1280 \
  --confidence 0.25 --nms-iou 0.7 --match-iou 0.5

# 3. 在同一原始预测上做结构门 vs 因果语义门配对 A/B
.venv/bin/python scripts/evaluate_15m_ma_launch_owner_yolo_semantic_gate.py \
  --raw-evaluation experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/raw/frozen_val_evaluation.json \
  --raw-predictions experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/raw/frozen_val_predictions.jsonl \
  --output experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/semantic_gate

# 4. 独立复算与哈希验收
.venv/bin/python scripts/verify_15m_ma_launch_owner_yolo_semantic_gate.py \
  --output experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/verification.json

# 5. 报告转 HTML
.venv/bin/python scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_owner_yolo_causal_semantic_gate_20260902.md \
  --out-dir analysis/html
```
