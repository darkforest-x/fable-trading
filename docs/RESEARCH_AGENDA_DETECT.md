# 检测层研究议程（H-DET）— 均线密集 + 盘口 tip

> **从属**：主议程 `docs/RESEARCH_AGENDA.md` 的 H-TIP 子簇。  
> **成功标准（发现级）**：强制 tip 窗 + `TIP_EDGE_BARS=2` 后贴边开火率相对 v12 **明显 >0**；  
> frozen-F1 不崩（回撤 ≤0.03 量级）。**确认级**仍只认 VPS 前向新鲜 100 笔。  
> **纪律**：不耗 holdout、不自动 promote；v13/v14 pad200 已终局，勿再抢训同构 pad200。

## 状态图例
🟢 已验证（发现级） · 🔴 已证伪 · 🟡 排队中 · 🔵 进行中 · ⚪ 未开始

## 人话总览（2026-07-22 晚）

| # | 人话 | 状态 | 今晚？ |
|---|---|---|---|
| H-DET-1 | pad200「框后无后文」训出的检测器，比 v12 更能在盘口 tip 贴边开火 | 🔴 **发现级未过**（v13+v14） | v14 MAD-on tip-smoke **0/27**、tip_hit **0.033**；见 `p_v14_pad200_train.md` |
| H-DET-2 | 把「有后文的中段簇」当硬负样本，能压住事后框 | 🟡 清单已备（v13 后再训） | 清单/预览已做；勿抢训 |
| H-DET-3 | 验收只看右缘 N 根有没有框，不只看 mAP | 🟢 已作必报口径 | — |
| H-DET-4 | MA 线宽/颜色/留白等渲染差会伤 tip | 🟡 线索有、消融未跑 | 协议已写；GPU 忙则不动 |
| H-DET-5 | tip 窗单独降 conf 能抬 tip_fire | 🔴 证伪 | — |
| H-DET-6 | tip-only 调度能抬 tip_fire | 🔴 证伪 | — |
| H-DET-7 | 离线 true_tip tip_hit ≠ 实盘 tip_fire（协议鸿沟） | 🟢 已证实 | — |
| H-DET-8 | A′ 贴边入账门能止血事后账，但**不**制造 tip | 🟢 工程通过 / 🔴 当 tip 解药 | — |
| **H-DET-EXT-*** | **外源启发簇**（右缘锚定 / 禁事后烛 / 流式口径 / 截断框 / 安全增广…） | 见下表 | 离线几何审计已做；**不抢 v13 GPU** |

## 假设表

| # | 假设（说人话） | 设计（单变量） | 判定 | 状态 |
|---|---|---|---|---|
| **H-DET-1** | **pad200**：把金标框右缘裁成窗末、左侧补满 200 根，正样本「无后文」→ 比 v12 提高 tip 贴边开火率 | v13：`dense_owner_v13_pad200`（MAD 关，后证实错窗）；**v14 复验**：`dense_owner_v14_pad200`（**MAD 开**）；基座 `owner_v12_htip`；对 `owner_best`(v12) | (a) true_tip tip_hit ≥ v12 且不崩 F1；(b) tip-smoke+tip_edge 开火 ≫ v12 的 0/27 | 🔴 **发现级未过（07-22）**：v13 tip_hit **0.008** / smoke **0/27**；**v14 MAD-on** tip_hit **0.033** / smoke **仍 0/27**（val mAP 0.155 仍不可当 tip 裁决）。标签错窗已修，pad200 协议本身未过线。`p_v13_pad200_train.md` · `p_v14_pad200_train.md` |
| **H-DET-2** | **硬负样本**：有后文的中段密集簇（模型爱事后框的那种）标成负/背景，抑制「等后文再框」 | 在 v12/v13 数据上**只加** hard-neg 集（或空标中段窗），其它不变 | tip-smoke 开火率↑ **或** 中段框率↓（账本 tip_edge_rejected / lag 分布），且 tip 正召回不塌 | 🟡 **清单已备**（2892 候选，见 `analysis/output/hardneg_mid_cluster/` + `PROTOCOL_train_after_v13.md`）；**未开训**；pad200 空标背景 ≠ 本假设 |
| **H-DET-3** | **右缘 N 根验收**：检测实验必报「窗末 N 根是否有框」，mAP 只作辅 | 评测：`tip_hit` / `bar_in_win ≥ 200−N`（现 N=2）；禁只用 mAP 宣称成功 | 与实盘 tip_fresh 同语义的发现级指标写入每份 p 报告 | 🟢 **已落地为口径**（v12 tip_hit、tip-smoke、tip_subset strict）；继续强制 |
| **H-DET-4** | **渲染差异**：MA 线宽/颜色/y 留白/`MIN_REL_SPAN` 与训练不一致时 tip 掉点 | 极小消融：固定权重+同窗，只改 `render.py` 一两项，比 tip 窗 conf/贴边命中 | 同几何下 tip 开火率相对基线变化 > 噪声 | 🟡 **开放**；夜报/tip_subset 提示「全序列 MA 重渲 tip_hit≪ true_tip 0.925」。GPU 占满时只跑协议不抢训 |
| **H-DET-5** | **tip 窗 conf 单独阈值**（如 TIP_CONF=0.22）能抬 tip_fire | 同权重、同贴边门，只改 tip 窗 conf | tip-smoke fired 与 lag-walk tip_fire 相对 0.30 提升 | 🔴 **发现级证伪（07-21）**：0/27 vs 0/27，账本 tip_fire 1/32 不变；`analysis/p_tip_only_smoke.md` |
| **H-DET-6** | **tip-only 调度**（只扫右缘窗）能抬 tip_fire | `FABLE_YOLO_MODE=tip` vs live | tip_fire / tip_fresh 提升 | 🔴 **发现级证伪（07-21）**：不抬出生率；可作 CPU 省窗，不作新鲜度解药；同报告 |
| **H-DET-7** | **协议鸿沟**：离线 true_tip tip_hit 高 ≠ 盘口 tip 能开火 | 对照：v12 tip_hit 0.925 vs tip-smoke 0/27 + box-to-bar（KORU/EDEN） | 若 offline 高而 live≈0 → 训练分布/几何语义仍错位 | 🟢 **已证实**；`p_v12_htip_eval` + `p_tip_only_smoke` + `p_box_to_bar_lag`。驱动 H-DET-1 |
| **H-DET-8** | **A′ 贴边入账**（最后 N=2 根才入账）能挡事后框进账本，但**不能**从零创造 tip | `TIP_EDGE_BARS=2`；KORU/EDEN 类应被拒 | tip_edge_rejected↑、事后 lag 行↓；tip_fire 仍可为 0 | 🟢 工程止血已上线 / 🔴 **当 tip 解药证伪**（不过滤≠产生 tip）；`HANDOFF` + `p_box_to_bar_lag` |

## 已入库证据（发现级，登记结论）

| 来源 | 对应假设 | 一句话结论 |
|---|---|---|
| `analysis/p_tip_only_smoke.md` | H-DET-5、H-DET-6 | tip-only / TIP_CONF 不抬 tip_fire；强制 tip 0/27 |
| `analysis/p_box_to_bar_lag.md` | H-DET-7、H-DET-8 | 映射无 bug；语义是「框启动区」→ tip−k；A′ 推荐并已上线 |
| `analysis/p_tip_subset_val.md` | H-DET-3、H-DET-7 | tip_strict 相对全量净折扣 **0.0465**；strict tip-hit ~3–4% |
| `analysis/p_v12_htip_eval.md` | H-DET-3、H-DET-7 | v12 true_tip tip_hit **0.925** / F1 0.650；≠ live |
| `analysis/output/diag_tip_smoke.json` | H-DET-5/6 | VPS v12：tip&live 均 **n_fired=0**（27 币） |
| `analysis/p_v13_pad200_train.md` | H-DET-1、H-DET-3 | v13 终局；tip-smoke 0/27；**勿用** val mAP 冒充 tip 裁决 |
| `analysis/p_v14_pad200_train.md` | H-DET-1、H-DET-3 | v14 MAD-on 复验；tip_hit 0.033 / tip-smoke 0/27；**勿再同构 pad200** |

## 训完后最小对照（H-DET-1，发现级）

```bash
# v14（MAD-on）终局对照：
bash scripts/eval_v14_vs_v12_tip.sh

# v13 历史：
bash scripts/eval_v13_vs_v12_tip.sh

# 或逐步：
PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \
  --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 \
  --weights models/owner_v13_pad200.pt \
  --out analysis/output/tip_rate_v13_pad200.json

# tip-smoke 需要账本符号对应的 K 线（本机常缺 → 优先 VPS 只读跑，或 --from-log 拷贝）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
  --weights models/owner_v13_pad200.pt \
  --out analysis/output/diag_tip_smoke_v13.json
```

**通过线（发现级）**：tip-smoke 贴边开火率相对 v12 **明显 >0**（不是只看 true_tip tip_hit）；  
再谈 owner 目视预览 / 影子权重。**禁止**自动 promote。

## H-DET-4 渲染消融协议（极小样，可 GPU 空闲时跑）

单变量每次只改一项；权重固定 `models/owner_best.pt`（v12）；窗=已知 tip 漏火币 3–5 张：

1. 基线：现行 `render.py`（MA `thickness=1`、`MIN_REL_SPAN=0.06`、MARGIN）  
2. 变体 A：MA 线宽 1→2  
3. 变体 B：`MIN_REL_SPAN` 0.06→0.03（更「捏」）  
4. 记录：raw 框数、tip_edge KEEP 数、max conf  

GPU 被 v13 占用时：**只写协议，不跑**。

## 外源启发簇（H-DET-EXT）— 2026-07-22

> 来源调研报告：`analysis/p_yolo_external_sources.md`。  
> 与内源 H-DET-1…8 **正交**：这里每条必须带外源出处；**禁止**「换 YOLO11 / 抄 ChartScan 权重」当假设。  
> 铁律：增强禁 fliplr/flipud/mosaic/mixup/hsv；不耗 holdout；不抢 `owner_v13_pad200`。

| # | 假设（说人话） | 外源 | 迁移改动（一句） | 发现级 / 确认级 | 状态 |
|---|---|---|---|---|---|
| **H-DET-EXT-1** | **右缘锚定**：实时形态的目标位置应固定在「最新时刻」一侧，而不是图中任意漂浮 | Chen & Tsai, *Dynamic Deep Convolutional Candlestick Learner* ([arXiv:2201.08669](https://arxiv.org/abs/2201.08669))：moving-window 下对象应落在 GAF 右下角（最新点） | 训练正样本框右缘贴窗末（pad200 / tip 金标）；评测禁把中段框当 tip 成功 | **发现**：标签几何审计 tip 右缘占比（已做：v13 train≈96% vs v11≈2.8%，见 `analysis/output/tip_box_geometry_vs_lit.json`）；**确认**：tip-smoke 贴边开火≫0 + 前向新鲜 100 | 🟡 几何已对齐训分布；等 H-DET-1 终局 |
| **H-DET-EXT-2** | **禁事后烛**：训练图若含信号**之后**的 K 线，模型学会「等走完再认」 | ENIAC 2025 Santos et al. *YOLO… with Moving Averages* ([DOI](https://doi.org/10.5753/eniac.2025.12471)) 明示 crossover **前后**烛都进图；ChartScanAI issues #2/#3 同构失败 | tip/pad 窗右缘=cut，cut 后 0 根；采集预览禁混「有后文」窗 | **发现**：审计正样本 `box_right→cut` 后剩余 bar 数=0（pad200 构建断言）；**确认**：同 H-DET-1 tip-smoke | 🟡 与 pad200 同向；勿抄 ENIAC「后文」 |
| **H-DET-EXT-3** | **流式口径**：离线 mAP/tip_hit ≠ 带延迟的在线成功；训推几何必须匹配部署延迟 | StreamYOLO / streaming perception ([arXiv:2207.10433](https://arxiv.org/abs/2207.10433), [GitHub](https://github.com/yancie-yjr/StreamYOLO))；sAP 联合延迟与精度 | 发现级主指标固定为 tip-smoke + `TIP_EDGE_BARS`，mAP 降级为辅（强化 H-DET-3） | **发现**：每份检测报告必报 tip-smoke（已作口径）；**确认**：VPS 前向新鲜 100（龄预算内） | 🟢 口径层；架构 DFP/TAL **不**移植 |
| **H-DET-EXT-4** | **截断框协议**：贴边物体只标**可见部分**、紧框，不臆测被裁掉的历史全长 | Ultralytics Academy [annotation best practices](https://academy.ultralytics.com/courses/dataset-readiness-for-yolo/annotation-best-practices)；Roboflow [YOLO training practices](https://blog.roboflow.com/best-practices-for-training-yolo/) truncation 一致性 | 盘口人工补标 / auto tip：框止于可见密集段，宽≈`MAX_DENSE_BARS`（12）量级，禁拉满整段盘整 | **发现**：框宽分布 vs 文献 5–16 bar（已做：tip_w p50≈0.05–0.07≈10–14/200）；**确认**：人工 tip 金标包 IoU 与 tip_fire | 🟡 协议可写入打标指南；大训不启 |
| **H-DET-EXT-5** | **MA 语义保留 + 无翻转增广**：均线进图抬召回；翻转/旋转会毁 K 线方向语义 | 同上 ENIAC 2025：加 MA 相对无 MA recall↑≤0.18；**刻意不用** flip/rotation，只用亮度/模糊/噪声/压缩 | 坚持六均线渲染；若增广只试「非颜色语义」项（亮度/压缩），**永不**开 fliplr/mosaic/hsv | **发现**：H-DET-4 渲染消融（GPU 空闲）；安全增广另开单变量小训；**确认**：tip-smoke 不降 | ⚪/🟡 与 H-DET-4 合流；禁抄其「事后烛」 |
| **H-DET-EXT-6** | **框几何→判断层**：检测框的宽/右缘偏移/conf 作数值特征，比再训一个 Buy/Sell YOLO 更贴本仓两层 | VT69 [Financial-Chart-Understanding-System](https://github.com/VT69/Financial-Chart-Understanding-System)：YOLO 框 + OHLCV 融合；本仓已有 2a→2b | 在 judgment 特征里**单变量**加 `box_w` / `box_right_offset` / `det_conf`（文件名带池名） | **发现**：val 时间切分 AUC/top-decile（不碰 holdout）；**确认**：前向 100 | ⚪ **等 tip_fire>0** 再立项 |
| **H-DET-EXT-7** | **形态窗长对齐文献**：密集框横跨约 5–16 根；过宽=巩固平台、过窄=噪声 | Chen & Tsai 数据集窗长 5–16；本仓 `MAX_DENSE_BARS=12`（E2.1） | 若 tip 仍≈0，单变量试 tip 子集 `MAX_DENSE_BARS` 8 或 16（只改一项） | **发现**：离线框宽已对齐 12（p50≈11–12 bar）；改阈值需 owner；小样 tip-smoke；**确认**：前向 100 | 🟡 审计支持现 12；改阈值须批准 |
| **H-DET-EXT-8** | **单向时序（多帧 tip）**：只允许过去→现在的通道，禁止未来帧特征 | TSM online / uni-directional shift ([arXiv:1811.08383](https://arxiv.org/abs/1811.08383), [mit-han-lab/temporal-shift-module](https://github.com/mit-han-lab/temporal-shift-module)) | 可选：同一币 tip−k…tip 多窗当「视频」微调（仅 past）；**不**上双向 TSM | **发现**：GPU 空闲后极小样；**确认**：tip-smoke | ⚪ 成本高；排在 EXT-1/2/4 之后 |

### 外源明确不立项（坑）

| 项 | 为何是坑 |
|---|---|
| ChartScanAI / foduucom Buy·Sell·形态权重 | 事后标签 + 默认增强；任务≠密集启动（见 `p_chartscanai_review.md`） |
| StreamYOLO 整栈 / DFP+TAL | 为自动驾驶「预测下一帧」；输入是视频不是 CSV→图；会引入未来监督叙事混淆 |
| ENIAC「crossover 后文烛」进训图 | 与铁律 3 / tip 无后文直接冲突 |
| Roboflow 杂类 candlestick 数据集当正样本 | 类定义是经典形态，不是六均线密集 |

## 优先队列（检测层）

1. **H-DET-4 / EXT-5** — H-DET-1（v13+v14）tip≈0 后优先：极小渲染消融（GPU 空闲）  
2. **H-DET-2** — 硬负中段簇（清单已备；开训需 owner 批）  
3. **H-DET-EXT-1/2/4** — 几何审计已完成；**禁止**再训同构 pad200 当解药（v14 MAD-on 已复验）  
4. **H-DET-EXT-6/7/8** — tip 起来后再排  
5. H-DET-1/5/6/8 — 已结案，勿复读当主药  

## 报告指针

- 本簇汇总：`analysis/p_yolo_dense_hypotheses.md`  
- 外源调研：`analysis/p_yolo_external_sources.md`  
- 主议程指针：`docs/RESEARCH_AGENDA.md` § D / 优先队列  
- **整仓旁路**（H-FE / H-TOOL / …，**不**混入本检测优先队列；tip 排队≠删除）：`docs/RESEARCH_AGENDA.md` § E · 扫描 `analysis/p_wuzao_topics_scan.md`（v2 整仓口径）

