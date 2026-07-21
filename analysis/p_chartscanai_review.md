# ChartScanAI 详细评测 — 对 fable-trading 有什么用

**日期**：2026-07-21  
**对象**：[Omar-Karimov/ChartScanAI](https://github.com/Omar-Karimov/ChartScanAI)（MIT 仓库；约 164★，2024-06 发布，此后基本无维护）  
**方法**：读 README / `app.py` / LICENSE / issues；用本仓 `.venv` 加载公开权重 `weights/custom_yolov8.pt` 读 checkpoint 元数据（**未**写入本仓、**未**跑生产、**未**打断任何训练）  
**对照**：本仓 2a 检测层（OKX 15m 均线密集 YOLO）+ tip/实盘路径（见 `HANDOFF.md`、`analysis/p_tip_only_smoke.md`）

---

## 结论先行

**对「盘口 tip 认不出」没有直接帮助。** ChartScanAI 和本仓撞上的是同一类坑：框往往标在形态**已经走完**之后，右缘/盘口几乎点不着火。社区 issue 明确写「只事后认」「实时信号滞后」——这正是本仓 tip 出生率≈0 的同构失败模式，不是解药。

可借鉴的只是**工作流与纪律提醒**（固定窗长、训推同渲染、实时 vs 事后分 conf、右缘验收），**不是**权重、Buy/Sell 标签、也不是他们那套默认 YOLO 增强。

---

## 一句话是什么

用 **YOLOv8** 在一张 **K 线图 PNG** 上画框，框的类别只有两个：**Buy** / **Sell**；外面套一个 **Streamlit** 网页，让人上传图或用 yfinance 画图再检测。  
灵感来自 IEEE Access 2020 论文 *YOLO … “Buy-Sell Decision” Model Over 2D Candlestick Charts*（Birogul / Temür / Köse），但仓库本身只交付**推理 Demo + 权重**，**没有**训练脚本、**没有**标注数据、**没有**回测/下单。

---

## 输入 / 输出（人话）

| 项 | ChartScanAI | 本仓 fable（2a） |
|---|---|---|
| 输入图 | 用户上传 PNG，或侧边栏用 **yfinance** 拉行情再 **mplfinance** 画蜡烛图 | OKX **15m** 缓存 → 本仓 `cv2` 渲染（蜡烛 + SMA/EMA 20/60/120） |
| 窗长 | 固定最新 **180** 根（作者 issue 回复也强调训推都是 180） | 固定 **200** 根；实盘 tip 窗右缘=盘口、无后文 |
| 品种 / 周期 | 任意 yfinance ticker；UI 可选 **1d / 1h / 1wk**（与训练分布是否一致无保证） | OKX 永续池（~344）；**只认 15m** |
| 输出 | 图上 Buy/Sell 框 + conf；默认 conf 滑条约 **0.30** | 单类 `dense_cluster` 框 → 映射到信号 bar → 判断层 LightGBM → 新鲜度门 / TG / 执行器 |
| 下游 | 给人看；无执行、无成本、无前向账本 | VPS 前向 + tip 实时路径 + 真仓（owner 授权） |

**权重实测（2026-07-21）**：

- 类名：`{0: 'Buy', 1: 'Sell'}`
- 底座：`yolov8m.pt` → `custom_yolov8.pt`（约 52MB）
- 训练记录：`epochs=150`，实际结果日志到 ~epoch 83；`imgsz=640`；`batch=8`
- val 指标（ckpt）：mAP50 ≈ **0.60**，P≈0.59，R≈0.60（远谈不上 README 宣传的 “High Accuracy”）
- **增强开着**：`fliplr=0.5`，`mosaic=1.0`，`hsv_h/s/v` 默认级——对本仓铁律 5 是反面教材

---

## 怎么训、怎么推理

### 训练（仓库外；只能从 README + 权重 + 论文拼）

1. **拉数**：yfinance（股票/加密）  
2. **画图**：mplfinance，`style="yahoo"`，约 180 根，无成交量，figsize≈(18, 6.5)，dpi=100  
3. **标注**：Roboflow **人工**框 Buy/Sell（作者 issue：多资产多周期，全靠手标）  
4. **训 YOLO**：Ultralytics YOLOv8m，`data_custom.yaml`（未开源），默认增强全开  

论文原版更极端：BIST 股票 **年线整图**（约 550 张年图、约 1 万个 Buy/Sell 标签），标签语义是「事后看支撑/阻力转折」——天然带**事后视角**。ChartScanAI 换了 YOLOv8 + Streamlit，但**标签哲学同构**。

### 推理（仓库里有的全部）

```text
上传/生成 PNG → YOLO(weights/custom_yolov8.pt).predict(conf=…) → plot 框
```

没有：滑动窗扫描、bar↔像素映射、贴边门、新鲜度、账本去重。  
作者对「实时」的官方说法（issue #2）：有些框在右缘出现，**更多要等形态走完才认**；建议实时用 conf **0.25–0.30**，更稳用 **0.40–0.50**。issue #3 标题直译用户结论：**「Model only renders retrospectively. Don't waste your time.」**

---

## 和 fable-trading 差在哪（对照表）

| 维度 | ChartScanAI | fable-trading |
|---|---|---|
| 任务定义 | 通用视觉 Buy/Sell 决策框 | **均线密集启动**区域检测（规则可复现） |
| 标签 | 人手主观；事后转折易泄漏 | `auto_label` 数值阈值 + 时间切分；H-TIP/v12 专啃右缘 |
| 渲染 | mplfinance yahoo（有标题轴样式）；**无六均线语义** | 白底纯蜡烛+六 MA；`MIN_REL_SPAN` 防低波动炸轴 |
| 增强 | fliplr + mosaic 开 | **强制关** flip/mosaic/mixup/hsv（旧 180 版死因） |
| 评估 | Demo 观感；无 holdout/置换/成本 | val 时间切分 + tip_hit / 前向 100 笔新鲜裁决 |
| 部署 | Streamlit Cloud 给人点 | VPS 脉冲 <15min；三门 30min；执行器 |
| 成功标准 | 「框看起来对」 | tip 盘口能开火 + 判断层净收益 + p 值 |

**一句话差**：他们做的是「给交易员看图找买卖点的玩具」；本仓做的是「密集启动候选机 + 判断层 + 实盘新鲜度」。表面都是「K 线图 YOLO」，目标函数完全不是一回事。

---

## 可借鉴清单

每条格式：**抄什么 / 怎么接到本仓 / 风险**。

1. **固定窗长纪律（180 根训推同构）**  
   - 抄什么：作者反复强调训练图全是 180 根，推理也裁最新 180。  
   - 接到本仓：继续坚持 tip/live **同一 200 窗几何**；采集 v13 tip 成败图时禁止混入「有后文」窗。  
   - 风险：无。本仓已做到；别被「多周期一起训」带偏。

2. **「实时 conf 低 / 事后 conf 高」分档叙事**  
   - 抄什么：作者建议实时 0.25–0.30、确认 0.40–0.50。  
   - 接到本仓：已有 `TIP_CONF` vs live 0.30；**诊断已证明**单降 tip conf **抬不动** tip_fire（`p_tip_only_smoke.md`）。可保留开关，别当主药。  
   - 风险：降 conf 只加噪声；不解决「无后文不画框」。

3. **右缘 / 末日 K 线决策规则（论文）**  
   - 抄什么：论文测试强调「只关心图表**最后一根**上的 Buy」才入组合。  
   - 接到本仓：语义对齐本仓 **tip 贴边门**（`TIP_EDGE_BARS=2`，`bar_in_win ≥ 198`）。可把「末日信号」写成评测口径：离线 tip_hit 必须用右缘无后文窗。  
   - 风险：他们仍用**事后标出来的** Buy 去训，再要求推理看右缘——目标与标签不一致；本仓必须用**无后文 GT**，不能抄他们标签生成法。

4. **人工复核工作流（Roboflow / Label Studio）**  
   - 抄什么：难例靠人手框，不靠神话 mAP。  
   - 接到本仓：走 `p_v13_real_tip_collect_plan.md`——先收 live tip 成败预览，Owner 目视 tip-miss-dense，再决定是否人手补标（已有 `datasets/label_live_tip_1000/` 空包）。  
   - 风险：人工 Buy/Sell **不能**混进密集标签；只标「盘口是否该有密集框」。

5. **训推同渲染（yahoo 风格一致性）**  
   - 抄什么：生成图与训练图同库同样式。  
   - 接到本仓：继续只用 `src/detection/render.py`；禁止把 TradingView 截图 / mplfinance 图丢给本仓 YOLO。  
   - 风险：若有人拿 ChartScanAI 权重在本仓渲染图上「试一下」会得出垃圾结论。

6. **社区失败案例当验收用例**  
   - 抄什么：issue #2/#3/#7（滞后、事后认、最后 5 根读不出来）。  
   - 接到本仓：把「右缘 N 根是否有框」写进每次检测实验必报（v12 tip_hit、tip-smoke 已在做）。  
   - 风险：无；这是免费负面教材。

7. **轻量 Demo 形态（可选，非主线）**  
   - 抄什么：Streamlit 一键看框，便于 Owner 目视。  
   - 接到本仓：已有/可扩 tip 预览脚本（`collect_v13_tip_previews.py`）；不必引入 Streamlit 依赖。  
   - 风险：Demo 容易诱导向「换个公开权重试试」——禁止进脉冲路径。

---

## 不建议抄的

1. **`custom_yolov8.pt` 权重**  
   任务（Buy/Sell）、渲染、周期、增强全错；且 ckpt 标注 Ultralytics **AGPL-3.0**（仓库 README 是 MIT，**权重链路另算**）。不得进 `models/ACTIVE` / `owner_best`，不得影子进脉冲。

2. **Buy/Sell 标签定义**  
   事后转折框 = 用未来形态教模型「现在该买」——和本仓「信号 bar 及之前」铁律冲突；也不能替代密集规则。

3. **默认 YOLO 增强（fliplr / mosaic / hsv）**  
   本仓铁律 5；旧项目已用血验证。他们开着还能只有 mAP50≈0.60，更说明别抄。

4. **把检测当最终交易决策**  
   本仓有判断层 + 成本 + 前向；他们没有。

5. **「High Accuracy」营销口径**  
   ckpt mAP50≈0.60；无时间切分前向、无扣费净收益。违反本仓「别把 AUC/mAP 当成功」。

6. **mplfinance yahoo 渲染管线**  
   丢掉六均线就丢掉本仓视觉语义；y 轴行为也与 `MIN_REL_SPAN` 教训无关。

7. **多周期混训同一权重再拿去 15m tip**  
   作者声称多周期训练，社区用 5m ETH 仍滞后——分布漂移 + 事后标签叠加。

8. **无人维护的「下一版实时/历史模式切换」**  
   issue 里承诺过，仓库自 2024-06 后无实质更新；不要等。

---

## 对「盘口 tip 认不出」有没有直接帮助

| 问题 | 答案 |
|---|---|
| 换上他们的权重能否立刻出 tip？ | **否**。类别/渲染/语义全不对。 |
| 他们的训练配方能否治 tip？ | **否**。他们用事后形态 + mosaic/flip；本仓 tip 要的是**无后文右缘**。 |
| 有没有可执行的间接启发？ | **有，且与现有结论同向**：必须专训/专评右缘；降 conf 不够；人工复核 tip-miss；固定窗、同渲染。这些已在 v12 H-TIP / tip-smoke / v13 采集计划里。 |
| 该不该为此开新实验分支？ | **不建议**。ROI 低于继续收真实 tip 成败图。 |

**最终判定**：ChartScanAI 是「K 线 YOLO Buy/Sell Demo」的参考实现，**验证了事后检出很容易、盘口很难**——对本仓是**警示样本**，不是**技术方案**。盘口 tip 继续走本仓主线：真实 tip 成败采集 → 右缘标签 →（如需）v13，而不是移植 ChartScanAI。

---

## 风险与诚实声明

- 未复现他们的 Roboflow 全集；训练数据规模/切分方式以作者口头描述为准。  
- 权重指标来自 ckpt 内嵌 `train_metrics`，不是本仓时间切分协议下的重评。  
- 未在 OKX 15m 本仓渲染图上跑他们的权重（故意：避免无意义伪实验污染日志）。  
- 未改生产代码、未动 holdout、未打断 v13/pad200 等训练任务。  
- IEEE 论文 PDF 全文部分章节经网页摘录；核心数字（550 年图、5161 Buy / 4848 Sell）来自可核对摘要段落。

---

## 来源

- https://github.com/Omar-Karimov/ChartScanAI  
- https://github.com/Omar-Karimov/ChartScanAI/issues/2（滞后 / 数据集问答）  
- https://github.com/Omar-Karimov/ChartScanAI/issues/3（事后渲染）  
- https://ieeexplore.ieee.org/document/9092995（灵感论文）  
- 本仓对照：`src/detection/render.py`、`src/detection/train.py`、`analysis/p_tip_only_smoke.md`、`analysis/p_v13_real_tip_collect_plan.md`
