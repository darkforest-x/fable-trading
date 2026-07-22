# 外源调研：YOLO「均线密集 / 盘口 tip」可迁移点子

**日期**：2026-07-22  
**动机**：Owner 批评上一轮 H-DET 几乎全是项目内复盘（pad200 / tip-only / A′），缺少前沿资料与开源对照。  
**方法**：先读仓内 `p_chartscanai_review.md` / `p_github_optimize_candidates.md` / `p_realtime_yolo_within_bar.md` / backlog 检测段，再 WebSearch+WebFetch 外网；本机 **CPU 离线**审计标签框几何（**未**抢 v13 MPS、**未**耗 holdout、**未**动 LIVE）。  
**议程登记**：`docs/RESEARCH_AGENDA_DETECT.md` § H-DET-EXT-\*

---

## 结论先行

外面**没有**「盘口 tip 均线密集」现成解药。公开物分成三类：

| 类 | 代表 | 对本仓 |
|---|---|---|
| K 线 YOLO Demo | ChartScanAI、foduucom 形态权重、Roboflow 杂标 | **坑为主**：事后标、开增强、任务≠密集启动 |
| 金融视觉论文 | Birogul 2020；ENIAC 2025（带 MA）；Chen & Tsai GAF-YOLO | **可迁移协议**：右缘锚定、禁翻转、MA 语义；**勿抄**事后烛 |
| 通用流式检测 | StreamYOLO、TSM online | **可迁移口径**：训推延迟匹配、只评因果窗；**勿整栈移植** |

**今晚真正便宜且已做完的发现级动作**：对照文献窗长，统计本仓 GT 框宽 / 右缘分布（见 §4）。  
**H-DET-1（v13）已终局未过线**（tip-smoke 0/27、true_tip 0.008；见 `p_v13_pad200_train.md`）。  
**强化警告**：v13 官方 val mAP≈0.027 **预期会烂**（val=未 pad 中段金标 vs train=pad200）——**禁止**用 val mAP 单独判 tip；也禁止用「val 烂」掩盖 tip-smoke 同烂的事实。

---

## 1. 仓内已读（避免空转）

| 文档 | 已结论（不重复做） |
|---|---|
| `p_chartscanai_review.md` | ChartScan = 警示样本；固定窗 / 同渲染 / 右缘评测可抄纪律，权重不可抄 |
| `p_github_optimize_candidates.md` | FiftyOne/CVAT/ONNX 是策展与加速，不治 tip_fire |
| `p_realtime_yolo_within_bar.md` | 瓶颈是 L3 几何，不是 TensorRT/DeepStream |
| backlog A | tip 通前不做换主线 / ChartScan 权重 |

---

## 2. 外面有什么（说人话）

### 2.1 值得借鉴的协议（不是换模型）

1. **右缘 = 最新时刻（Chen & Tsai, arXiv:2201.08669）**  
   他们把实时 moving-window 检测改成：对象永远在时间轴**最新点**（GAF 右下角），并简化网格。  
   → 与本仓 tip / pad200「框贴窗末」同构。外源独立印证：自由漂浮框适合事后回看，不适合实盘。

2. **流式成功 ≠ 离线 mAP（StreamYOLO, CVPR 2022 / arXiv:2207.10433）**  
   自动驾驶里用 sAP 把延迟和精度捆在一起；训练输入只用过去+现在。  
   → 本仓对应物已是 tip-smoke + 新鲜度门；外源支持「别用 val mAP 宣称 tip 通了」。  
   → **不**建议接 Dual-Flow / Trend-Aware Loss（视频下一帧预测，错配 CSV→图脉冲）。

3. **截断物体只标可见段（Ultralytics / Roboflow 标注规范）**  
   贴边目标：紧框、可见部分、规则写死且一致。  
   → tip 金标应是「盘口仍可见的密集段」，不是把整段历史巩固拉成大框。

4. **MA 进图有用；翻转有害（ENIAC 2025）**  
   蜡烛+均线相对纯蜡烛，Buy/Sell YOLO recall 可高约 0.18；他们**故意不用** flip/rotation，只用亮度/模糊/噪声/压缩。  
   → 强化本仓铁律 5 + 六均线渲染；若将来试增广，只试非 hsv/非 flip 项（H-DET-EXT-5）。

5. **YOLO 框 + 数值特征融合（VT69 Financial-Chart-Understanding）**  
   视觉检出后再与 OHLCV 融合做 regime。  
   → 本仓已是 2a→2b；可迁的是把 **框宽/右缘偏移/conf** 显式进 LGBM（H-DET-EXT-6），等 tip 开火后再立项。

### 2.2 坑（看起来像解药）

| 坑 | 出处 | 为何踩不得 |
|---|---|---|
| 公开 Buy/Sell / 形态权重 | ChartScanAI、foduucom HF、Roboflow Universe | 类定义错；增强常开；事后形态 |
| 训练图带「事件后」K 线 | ENIAC 数据集描述；Birogul 年线事后转折 | 教模型等走完——正是 tip 死因 |
| 把 StreamYOLO/DeepStream 当 tip 药 | StreamYOLO、Savant | 治延迟叙事，不治「无后文不画框」 |
| 「升级 YOLO11」当假设 | 多篇金融 YOLO 版本扫 | 无单变量归因；本仓底座已 Ultralytics |

---

## 3. 可迁移假设（摘要 → 议程全文）

详见 `docs/RESEARCH_AGENDA_DETECT.md` H-DET-EXT-1…8。Owner 最该盯的 5 条：

| 优先 | ID | 一句话 | 要不要现在训 |
|---|---|---|---|
| 1 | EXT-1 | 右缘锚定（外源印证 pad200） | 否；等 v13 评 |
| 2 | EXT-2 | 禁事后烛 | 否；pad200 已约束；审计即可 |
| 3 | EXT-3 | 流式口径（tip-smoke 主指标） | 否；已是纪律 |
| 4 | EXT-4 | 截断紧框 / 5–16 bar 窗长 | 否；离线已对齐 |
| 5 | EXT-5 | MA 保留 + 安全增广边界 | GPU 空闲才碰 H-DET-4 |

---

## 4. 不抢 GPU 的发现级小测（已做）

**问题**：文献建议形态窗约 **5–16** 根；截断协议要求 tip 框贴右且不要过宽。本仓 GT 是否同向？

**数据**：`datasets/dense_owner_{v11,v12_htip,v13_pad200}/labels/{train,val}`（只读标签，无推理）。  
**产物**：`analysis/output/tip_box_geometry_vs_lit.json`

| 集 | 框数 | 右缘 ≥0.95 占比 | 框宽 p50（≈bar/200） | tip 子集宽 p50 |
|---|---|---|---|---|
| v11 train | 4244 | **2.8%** | 0.055（~11 bar） | 0.051 |
| v12_htip train | 8402 | **49%** | 0.062（~12 bar） | 0.067 |
| **v13_pad200 train** | 4146 | **96%** | 0.058（~12 bar） | 0.058 |
| v11 / v13 **val** | 1587 | **2.1%** | 0.054 | — |

**解读**：

1. **框宽**：与文献 5–16 及本仓 `MAX_DENSE_BARS=12` **对齐**（H-DET-EXT-7 暂不改阈值）。  
2. **右缘**：v13 train 几乎全部 tip 锚定（EXT-1 数据侧达标）；v11 几乎全是中段——解释「离线能认、盘口不能」。  
3. **val**：v13_pad200 的 val 标签与 v11 val **逐文件相同**（3169/3169）——这是「frozen F1 对照用中段金标」的设计取舍，**不是 tip 分布**。因此 **H-DET-1 发现级必须看 tip-smoke / true_tip，不能看 val mAP 冒充 tip 成功**（与 EXT-3 / H-DET-7 一致）。

**未做（故意）**：任何 YOLO 前向、任何新训、任何 holdout。

---

## 5. 与 v13 / 今晚调度

| 何时 | 做什么 |
|---|---|
| **现在** | H-DET-1 已评：tip-smoke 仍 0；**勿**再盯 val mAP |
| **下一步** | H-DET-4 / EXT-5 渲染消融（GPU 空闲）；再议 H-DET-2 硬负（须 owner） |
| EXT-7 改 `MAX_DENSE` | 须 owner；暂不排 |
| tip_fire>0 后 | 再谈 EXT-6（框特征进 2b）、ONNX、FiftyOne 策展 |

---

## 6. 风险与诚实声明

- 外网金融 YOLO 几乎全是 Buy/Sell 或经典形态；**没有** OKX 15m 六均线密集开源金标。  
- ENIAC 全文部分站点 403；结论来自可抓取摘要/章节与 DOI 元数据，核心数字（MA→recall、禁 flip）已交叉核对。  
- StreamYOLO / TSM 来自驾驶与视频；迁移的是**因果与评测哲学**，不是代码。  
- v13 val=未 pad 是构建选择；若有人用 val mAP 宣称 tip 改善，属指标作弊。  
  对称：**也不能**只用 val mAP 崩宣称 tip 失败——须 tip-smoke（07-22：两者都烂，分开记账）。  
- 本报告不改变 ACTIVE / frozen / LIVE / 三门。

---

## 7. 来源（可点开）

- https://arxiv.org/abs/2201.08669 — Dynamic Deep Convolutional Candlestick Learner  
- https://doi.org/10.5753/eniac.2025.12471 — YOLO Buy/Sell with Moving Averages (ENIAC 2025)  
- https://arxiv.org/abs/2207.10433 — StreamYOLO；https://github.com/yancie-yjr/StreamYOLO  
- https://arxiv.org/abs/1811.08383 — TSM（uni-directional online）  
- https://github.com/VT69/Financial-Chart-Understanding-System  
- https://github.com/Omar-Karimov/ChartScanAI（仓内已评）  
- https://academy.ultralytics.com/courses/dataset-readiness-for-yolo/annotation-best-practices  
- https://blog.roboflow.com/best-practices-for-training-yolo/  
- 仓内：`p_chartscanai_review.md`、`p_github_optimize_candidates.md`、`auto_label.py`（`MAX_DENSE_BARS=12`）

---

## 8. 下一步（需 Owner 决策的标出）

1. **默认（无需批）**：等 v13 → tip-smoke 对照。  
2. **若 H-DET-1 失败**：批准启动 H-DET-4 渲染消融（本机，单变量）。  
3. **阈值**：改 `MAX_DENSE_BARS`（EXT-7）→ **需批准**。  
4. **否决**：ChartScan/foduu 权重、StreamYOLO 整栈进脉冲、自动 promote。
