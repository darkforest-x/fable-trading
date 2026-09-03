# 15m Grade-A 六均线 close→HL2 单变量复训（2026-09-03/04）

性质：P1 完成历史图像检测表示消融；Owner 明确授权的一次例外训练。
结论等级：**pre-holdout、单 seed、非生产**。
边界：**未读取 `2026-05-04` 起 holdout，未调阈值，未 promote、未部署，未改 ACTIVE/frozen/forward，未下单。**

## 结论先行

**HL2 有“正例更容易被检出”的迹象，但没有证明整体优于 close，本轮不建议替换现有表示。**

- 最早可用窗事件召回由 `126/155 = 81.29%` 升至 `135/155 = 87.10%`，增加 **5.81pp**；原始配对 `p=0.0490`，但三项主检验 Holm 校正后为 `p=0.1471`，没有过 `0.05`。
- post2 事件召回由 `122/149 = 81.88%` 升至 `130/149 = 87.25%`，增加 **5.37pp**；Holm `p=0.1536`，同样没有过门。
- 空标签负样本开火由 `41/3600 = 1.139%` 增至 `51/3600 = 1.417%`，增加 **0.278pp / 10 张**。事件簇检验 `p=0.4446`，无法确认差异非随机，但预注册规则要求的是“观察值不得增加”，因此这一门也失败。
- 所以预注册联合裁决是 `demonstrated_hl2_improvement=false`：**正例主门没有校正后显著改善，负例误触观察值又上升。** mAP 或次级指标不能覆盖这两道失败。
- 单轮训练的最佳 epoch 仍为 6。相对 close，HL2 的 `P`、`R`、`mAP50` 上升，但 `mAP50-95` 从 `0.78602` 降至 `0.77609`，说明“较松 IoU 下更容易找到”与“更严格框定位质量”并未同向改善。

这不等于 HL2 没价值。结果更像：**HL2 让模型整体更愿意开火，尤其 SHORT 正例受益，但同时多报了负样本；在当前单 seed 下还不能区分这是稳定的表示收益，还是训练波动／更激进的检测倾向。**

## 1. 实验问题与唯一变量

问题是：在样本、时间切分、K 线、坐标变换、颜色、线宽、六线顺序、基础权重与训练配方全部冻结时，把六条均线的数据源从 `close` 改成

```text
hl2 = (high + low) / 2
```

是否能提高 Grade-A `dense_long / dense_short` 检测。

六条线仍然是：

```text
SMA20 / EMA20 / SMA60 / EMA60 / SMA120 / EMA120
```

没有改成 RMA、HMA、WMA，没有改周期，没有改 K 线颜色，也没有改 1px 线宽。这保证本轮只研究 **close→HL2 这一套完整视觉表示**。

但这里有一个必须诚实写出的细节：旧框的纵向范围按“核心完整影线 + 六条均线 + 4% padding”定义。HL2 改变了线的位置，若强行复制旧纵框，`515/8000` 个正例核心会有新均线落在框外。因此本轮按原规则机械重算纵框：

- 类别、核心起止、横向框完全不变；
- `5325/8000` 个正例纵框有变化；
- 纵边最大变化中位数 `1px`、P95 `7px`、最大 `25px`。

所以本轮估计的是“**HL2 像素 + 为保持同一标注语义而必需的纵框更新**”的整体效果，不是纯像素源的实验。完整冻结契约见 [preregistration.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/preregistration.json)。

## 2. 数据与完整性

| 项目 | train | chronological val | 合计 |
|---|---:|---:|---:|
| 正例图 | 6,800 | 1,200 | 8,000 |
| 负例图 | 20,400 | 3,600 | 24,000 |
| 总图数 | 27,200 | 4,800 | 32,000 |
| 正例事件 | 888 | 155 | 1,043 |
| 负例事件 | 2,664 | 465 | 3,129 |

val 正例方向为 LONG `531`、SHORT `669`；负例为 hard `2,400`、easy `1,200`。全部窗口时间为 `2020-02-07 09:45Z` 至 `2026-05-03 20:30Z`，最大窗口右端严格早于 holdout 起点 `2026-05-04 00:00Z`。

数据集完整性结果：

- manifest SHA-256：`ec93d6bfd04cc84a24a34cd745af2c74943f9a75e664831060619511ba60f6d7`；
- `32,000/32,000` 图像可解码且图像哈希唯一；
- close 回放零假设 `32,000/32,000` PNG 逐字节复现 baseline，说明 builder 在 source=close 时没有引入额外渲染漂移；
- HL2 相对 close 为 `32,000/32,000` 图像变化，中位变化像素占比 `3.286%`；
- 24,000 个负标签逐字节不变，正例横向框变化为 0；
- 本地与 3060 远端均全量核验 64,000 个图像／标签文件。

证据：[dataset_build_receipt.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-v1/results/dataset_build_receipt.json)、[independent_qa_receipt.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-v1/results/independent_qa_receipt.json)、[remote_dataset_preflight.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/remote_dataset_preflight.json)。

## 3. 训练执行与冻结配方

训练于 `2026-09-03 20:56:01 CST` 在 RTX 3060 12GB 启动，于 `2026-09-04 07:00:54 CST` 正常结束；远端 exit code 为 0，`results.csv` 有连续 `1–40` 共 40 行，没有 early stop。训练耗时约 `10.058h`。

冻结配方为官方 `yolo11s.pt`、`imgsz=1280`、epochs 40、patience 0、batch 8、AdamW、`lr0=1e-4`、`lrf=.01`、warmup `.5`、seed 0、deterministic、rect；flip/mosaic/mixup/HSV 全关，translate `.02`、scale `.1`。取回的 `args.yaml` 与预注册逐项一致。

关键 SHA-256：

| 产物 | SHA-256 |
|---|---|
| treatment `best.pt` | `0533d48f209bfeb73b10f147a38f245de1765599ad222770af394c4f0f68ab52` |
| `args.yaml` | `2a7a2c43729fe931c30192f54e631e63372317dacfb853de80b02a5683b270b0` |
| `results.csv` | `c98ad6476ef153bc2315b6cb9893217fc8da151df1497a2bbb71ad14133b13e5` |
| `train.log` | `11a56c11c2406ecebce5cc9891b235ba9762b9ff367fa6d767f9e6d085b60809` |

完整收据见 [training_receipt.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/training_receipt.json)。

### 3.1 Ultralytics val 曲线对照

下表比较各自 `results.csv` 中由冻结 fitness 选出的最佳 checkpoint；两臂最佳都在 epoch 6。

| 指标 | close baseline | HL2 treatment | HL2 − close |
|---|---:|---:|---:|
| Precision | 0.85199 | 0.89665 | **+0.04466** |
| Recall | 0.86705 | 0.87151 | **+0.00446** |
| mAP50 | 0.92236 | 0.93799 | **+0.01563** |
| mAP50-95 | **0.78602** | 0.77609 | **−0.00993** |

这些训练期指标是 secondary：它们有助于理解优化曲线，但不是本轮固定阈值的主裁判。

## 4. 冻结 val 配对结果

两臂都只在冻结 chronological pre-holdout val 上，以同一环境与固定参数推理：Mac `.venv`、Python 3.9.6、Torch 2.8.0、Ultralytics 8.4.89、NumPy 2.0.2、MPS、batch 8、`imgsz=1280`、`conf=0.25`、NMS IoU `0.7`、真命中 IoU `0.5`。阈值未搜索。

### 4.1 预注册主指标

| 主表面 | close | HL2 | 差值 | 95% 事件块 bootstrap CI | 原始 p | Holm p | 通过？ |
|---|---:|---:|---:|---:|---:|---:|---:|
| 最早可用窗事件召回 | 126/155 = 81.29% | 135/155 = 87.10% | **+5.81pp** | [+0.65pp, +10.97pp] | 0.0490 | 0.1471 | 否 |
| post2 事件召回 | 122/149 = 81.88% | 130/149 = 87.25% | **+5.37pp** | [0.00pp, +10.74pp] | 0.0768 | 0.1536 | 否 |
| 负样本开火率 | 41/3600 = 1.139% | 51/3600 = 1.417% | **+0.278pp** | [−0.361pp, +0.893pp] | 0.4446¹ | 0.4446 | 否 |

¹ 负例主 p 值按 465 个 `negative_event_id` 做事件簇 Monte Carlo sign-flip；单图 McNemar `p=0.2026` 仅作描述，不能把同事件变体当独立样本。

discordant pair 也支持同一解释：最早窗为 control-only 4、HL2-only 13；post2 为 4 对 12；负例为 20 对 30。HL2 在正例上赢得更多，但也在负例上新增更多开火。

### 4.2 次级诊断

| 次级表面 | close | HL2 | 差值 | 未校正配对 p / CI |
|---|---:|---:|---:|---:|
| 任一变体事件召回 | 142/155 = 91.61% | 149/155 = 96.13% | +4.52pp | p=0.0391；CI [+1.29pp, +8.39pp] |
| 正例图召回 | 1020/1200 = 85.00% | 1079/1200 = 89.92% | +4.92pp | p=2.56e-7；CI [+1.41pp, +8.66pp] |
| LONG 图召回 | 457/531 = 86.06% | 474/531 = 89.27% | +3.20pp | p=0.0640；CI [−3.01pp, +9.36pp] |
| SHORT 图召回 | 563/669 = 84.16% | 605/669 = 90.43% | +6.28pp | p=7.46e-9；CI [+2.39pp, +10.81pp] |
| 负例框 / 1000 图 | 11.39 | 14.17 | +2.78 | CI [−3.61, +8.96] |

这些是诊断，不进入预注册通过规则。尤其 SHORT 改善很强，但它是在看过结果后的次级分层，不能单独授权切换表示。

### 4.3 非方向性实验的零假设对照

本轮是检测表示实验，不读交易 outcome，因此 val AUC、置换检验 p、top-decile 毛／净收益、胜率、成本和“同币×同时间块×同波动桶随机入场”在字面上都不适用；硬填这些指标会制造不存在的交易结论。

同等严格的零假设对照有两层：

1. **方向翻转 null**：保持所有框、置信度和 IoU 不变，只交换 LONG/SHORT 类别。真实方向事件命中 `149/155`，翻转后 `0/155`，配对 `p=2.80e-45`；真实方向图命中 `1079/1200`，翻转后 0。说明模型不是靠不分方向的任意重叠拿到这些召回。
2. **配对表示 null**：同一 sample/event 顺序逐项比较 close 与 HL2，用 exact discordance test、事件簇 sign-flip、Holm family correction 和 20,000 次事件块 bootstrap。正式结果保存在 [paired_val_comparison.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/paired_val_comparison.json)。

## 5. 推理环境纠偏

首次 treatment 推理在 RTX 3060 CUDA 上运行，但归档 baseline 账本实际来自 Mac MPS。比较器在写正式结果前拒绝了这次跨环境配对。随后 treatment 在 baseline 的完整 MPS 环境重跑，正式比较只使用 MPS 账本；CUDA 账本保留为 supplemental。

两套 treatment 账本的 4,800 个 sample ID 与所有离散框／类别／命中结果恰好一致，但 `1,257` 个置信度浮点值不同，最大绝对差 `0.003553`。这证明“聚合计数碰巧一样”仍不能替代运行时一致性。纠偏证据见 [evaluation_environment_correction_receipt.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/evaluation_environment_correction_receipt.json)。

## 6. 解读

1. **关于“HL2 会不会更平滑”**：HL2 确实削弱了收盘价相对当根高低区间的端点噪声，但它不等价于把 MA 算法改成更强平滑器。周期与 SMA/EMA 递推完全没变；本轮只能说明这种输入表示让该次模型更容易检出正例。
2. **关于为什么 mAP50 上升、mAP50-95 下降**：HL2 的框在较松 IoU 上更容易被找到，但严格定位质量稍差；机械重算的纵框也使 estimand 包含轻微几何变化。不能把 mAP50 单独解释成“线更好”。
3. **关于模型是否真的更准**：如果“准”只指召回，HL2 的数字有希望；如果“准”要求正例检出与空背景误触共同改善，本轮没有通过。
4. **关于颜色和线宽**：本轮没有触碰它们，不能从这里推断单色 K 线或同色均线会更好。颜色承载 LONG/SHORT 与不同周期身份，贸然同色可能去掉有用语义。线宽应作为下一条独立变量另测，不能与 HL2 打包。

## 7. 风险与诚实声明

- **只有一个训练 seed（0）**：配对推理的 p 值衡量这两个已训练模型在冻结 val 上的样本差异，不包含跨 seed 训练方差。它不能证明 +5–6pp 会在重训后稳定复现。
- **不是纯像素实验**：为遵守相同标注语义，5,325 个正例纵框机械变化；因此无法把效应全部归因给 HL2 线条本身。
- **同一历史事件有多个图**：正式不确定性已按事件分块；单图级极小 p 值仅为次级描述。
- **completed-history ≠ live tip**：本轮窗口包含形态后的确认上下文，不能冒充 tip/tip-1/tip-2 实盘新鲜检测器，也不具生产资格。
- **没有交易收益结论**：未加载未来收益与障碍标签，不能从检测 recall 推导 PF 或净收益。
- **没有消费 holdout**：本配置仍为 0 次 holdout；不得把这份报告当最终泛化验收。
- **没有状态变更**：ACTIVE、frozen、forward、部署和下单均未改变。

## 8. 裁决与下一步选项

当前裁决：**保留 close 作为基准；HL2 记为“有希望但未证实”，不 promote、不部署。**

需要 Owner 决策的后续选项：

1. **推荐：先停在这里。** 结果已回答“值得不值得直接换”：不值得直接换。
2. 若要验证它是不是训练随机性，另行批准 **3 个或更多独立 seed 的同配方 paired replication**；提前冻结 seed 列表，并以事件级汇总的联合召回／误触门裁决。该动作属于新训练，当前授权不自动覆盖。
3. 若目标仍是像素可见度，单独做 `1px → 2px` 六线线宽实验；保持 source=close，不与 HL2、颜色或 MA 类型打包。
4. 不建议把所有 K 线和六条均线改成同色后直接训练。先做无训练的信息损失／可见度审计，再决定是否值得占用训练预算。

## 9. 从零复现命令

以下命令只面向冻结 pre-holdout 数据。不要添加任何 holdout 路径或改阈值。

```bash
cd /Users/zhangzc/fable-trading

# 1) 从冻结 baseline manifest 重建 HL2 treatment，并独立核验
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_owner_grade_a_hl2.py
PYTHONPATH=. .venv/bin/python scripts/verify_15m_ma_launch_owner_grade_a_hl2.py

# 2) 在已核验 RTX 3060 上执行冻结 full40/native-1280 配方；完成后取回四项核心产物
bash scripts/train_15m_ma_launch_owner_grade_a8000_neg24000_hl2_full40_1280_on_3060.sh
bash scripts/train_15m_ma_launch_owner_grade_a8000_neg24000_hl2_full40_1280_on_3060.sh --fetch

# 3) 在与 baseline 相同的 Mac MPS 环境做固定阈值 treatment 推理
PYTHONPATH=. .venv/bin/python scripts/evaluate_15m_ma_launch_owner_grade_a8000_val.py \
  --weights analysis/output/ma_launch_owner_grade_a8000_neg24000_hl2_v1/ma_launch_owner_grade_a8000_neg24000_hl2_v1_y11s_ft1280_full40/weights/best.pt \
  --weights-sha256 0533d48f209bfeb73b10f147a38f245de1765599ad222770af394c4f0f68ab52 \
  --expected-manifest-sha256 ec93d6bfd04cc84a24a34cd745af2c74943f9a75e664831060619511ba60f6d7 \
  --dataset datasets/ma_launch_owner_grade_a8000_yolo_neg24000_hl2_v1 \
  --output experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/frozen_val_evaluation.json \
  --predictions-output experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/frozen_val_predictions.jsonl \
  --experiment-id exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1 \
  --generator-commit cb9140148b41b3d8906bcf869f3fa666024d3139 \
  --device mps --batch 8 --imgsz 1280 --confidence 0.25 --nms-iou 0.7 --match-iou 0.5

# 4) 与归档 baseline 账本逐样本、逐事件配对比较
PYTHONPATH=. .venv/bin/python scripts/compare_15m_ma_launch_paired_val.py \
  --control-evaluation experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/raw/frozen_val_evaluation.json \
  --control-predictions experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1/results/raw/frozen_val_predictions.jsonl \
  --treatment-evaluation experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/frozen_val_evaluation.json \
  --treatment-predictions experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/frozen_val_predictions.jsonl \
  --control-manifest-sha256 22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2 \
  --treatment-manifest-sha256 ec93d6bfd04cc84a24a34cd745af2c74943f9a75e664831060619511ba60f6d7 \
  --control-weights-sha256 862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838 \
  --treatment-weights-sha256 0533d48f209bfeb73b10f147a38f245de1765599ad222770af394c4f0f68ab52 \
  --output experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/paired_val_comparison.json \
  --generator-commit fbbe06440930636f1c01add921c15189a9be6455 \
  --bootstrap-reps 20000 --permutation-reps 100000 --seed 20260903

# 5) 报告转 HTML
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_owner_grade_a8000_neg24000_hl2_train1280_20260903.md \
  --out-dir analysis/html
```

## 10. 关键产物

- treatment 固定阈值评估：[frozen_val_evaluation.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/frozen_val_evaluation.json)
- treatment 逐图预测账本：[frozen_val_predictions.jsonl](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/frozen_val_predictions.jsonl)
- 正式配对裁决：[paired_val_comparison.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/paired_val_comparison.json)
- 训练完整收据：[training_receipt.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/training_receipt.json)
- 推理环境纠偏：[evaluation_environment_correction_receipt.json](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-hl2-train1280-full40-v1/results/evaluation_environment_correction_receipt.json)
