# 为什么 v13 训这么差？训练集诊断 — 2026-07-22

**结论先说**：训练集**没有大规模标签/渲染崩坏**；正样本框按设计贴右、K 线/MA 正常、白底与 v11 同款。  
**差主要不是「val 口径吓人」单独造成**——val mAP 预期会烂，但 true_tip tip_hit 0.925→0.008 与 tip-smoke 0/27 说明**模型在 tip 协议上也几乎不点火**（多数窗 conf=0.3 下 0 框）。  
**未** promote；**未**开新大训。

## 1. 训练集数字（`datasets/dense_owner_v13_pad200` train）

| 项 | v13 pad200 | v11（对照） | v12 htip（对照） |
|---|---:|---:|---:|
| 标签文件 | 8467 | 8561 | 12520 |
| 正样本文件（有框） | **3947** | 4041 | 8000 |
| 空标背景 | **4520**（与 v11 同拷） | 4520 | 4520 |
| 框数 | 4146 | 4244 | 8402 |
| 框右缘 p10/p50/p90 | **0.992 / 0.992 / 0.992** | 0.154 / 0.509 / 0.849 | 0.250 / 0.923 / 0.997 |
| 右缘≥0.95 占比 | **96.0%** | 2.8% | 49.2% |
| 右缘∈[0.4,0.9) 占比 | 2.6% | 55.9% | 29.5% |
| 框宽 p10/p50/p90 | 0.038 / 0.058 / 0.073 | 0.036 / 0.055 / 0.067 | 0.039 / 0.062 / 0.077 |
| 主框仍中段（max-w 未贴右） | **51 / 3947（~1.3%）** | — | — |
| 极扁 h&lt;0.02 / 极高 h&gt;0.5 | 40（1.0%）/ 25（0.6%） | — | — |

构建摘要（`pad200_summary.json`）：`train_pad200=3947`，`train_skip=94`，`train_bg_copied=4520`，`val_policy=copy_orig_unchanged`。

**val（故意未 pad）**：3169 文件 / 1509 正 / 1587 框；右缘中位 **0.509**，≥0.95 仅 **2.1%**——与 v11 val 同几何尺子。这是 val mAP≈0.027 的结构性原因，**不是 tip 裁决**。

## 2. 渲染抽查（人话）

- 本项目图历来是**白底** + 绿涨红跌 + 多色 MA（v11 / v13 corner RGB=255），**不是**黑底；v13 相对 v11 **无底色回归**。
- 抽查 80 张正样本：均有非黑像素（K/MA），未见空白图批量污染。
- Owner 目视包：`analysis/output/v13_train_sample20/`（见下）。

## 3. 诚实归因：标签错 vs val 口径 vs 真学崩

| 层级 | 判断 | 证据 |
|---|---|---|
| A. 大规模标签坏 / 渲坏 | **否** | 96% 贴右；框宽≈v11；白底/MA 正常；极端框 &lt;2% |
| B. 官方 val mAP 烂 | **预期内，不可当 tip 判决** | train tip vs val 中段错位；见 `docs/learnings/v13-val-map-is-not-tip-verdict.md` |
| C. tip_hit / tip-smoke | **真失败** | tip_hit **0.008**（1/120）；head 里约 29/30 窗 **n_boxes=0**；tip-smoke **0/27**=v12 |

**主因排序（假设，非已证实因果）**

1. **纯 pad200 + 中段 val early-stop → tip 能力 catastrophic forgetting**：从 v12 底座训，train 几乎只剩贴右正样本，val 却是中段金标；全程 mAP 贴地（best≈0.027），选出的 ep22 在 true_tip 重渲上接近「不报框」。  
2. **训推几何/渲染协议差（H-DET-4 候选）**：pad200 是 crop-after-box 重渲；true_tip 评测是另一套 tip 重渲——v12 能打 tip_hit，v13 几乎 0，符合「过拟合 pad200 窗貌 / 丢通用 tip」。  
3. **去掉中段正样本失去 v12 混合归纳**：v12 tip 克隆仍保留大量中段（右缘≥0.95 仅约一半），可能比「纯贴右」更稳；纯 tip 未必可学。  
4. **少数脏标签（中段残留 ~1%、极扁/极高 &lt;2%）**：不足以解释 tip_hit 两个数量级崩盘。  
5. **空标背景**：与 v11 同数拷贝，不是 v13 新病因（也≠ H-DET-2 中段硬负）。

## 4. 20 张图怎么打开

```bash
open analysis/output/v13_train_sample20/index.html
# 或
open analysis/output/v13_train_sample20/annotated
```

| 路径 | 内容 |
|---|---|
| `analysis/output/v13_train_sample20/index.html` | 浏览器画廊（原图+GT） |
| `…/annotated/*.png` | GT 叠框（黄线 x=0.95） |
| `…/raw/*.png` | 原图 |
| `…/README.md` / `manifest.json` | 选样理由 |

选样：典型贴右约半；混入中段残留、过窄/过宽、极扁、极高、多框、空标背景各若干。

## 5. 风险与诚实声明

- 未耗 holdout；未改 ACTIVE / frozen；未清 forward_log。  
- 未对 3947 张全量目视——20 张是分层抽查。  
- tip_rate 细节只落了 `details_head`；全量若需可重跑 `scripts/eval_v13_vs_v12_tip.sh`。  
- 「黑底」若 Owner 记忆来自别的渲染器：本仓 dense_owner 一直是白底。

## 6. 下一步（需 Owner 点头）

1. 默认：主线仍 v12；H-DET-1 保持 🔴。  
2. 排队 H-DET-4 渲染消融（单变量，GPU 空闲）。  
3. 可选 H-DET-2 中段硬负（空标背景≠硬负）。  
4. **禁止**：因 val mAP 再盲训一轮 pad200；禁止自动 promote。

复现诊断数字：`analysis/output/v13_train_diag_stats.json`。
