# P1：15m 均线密集 A 级正样本扩容到 8,000 张（2026-08-28）

## 结论

已按 Owner 的要求，不再只从原 10,000 张里挑，而是扩展历史数据重新筛选并完成一套 **8,000 张 A 级正样本数据集**：

- 新拉取并校验 Binance USD-M 永续 15m 历史：638 个有效交易对、49,162,351 根 K，范围 `2020-01-01` 至 `2026-04-30`；
- 严格复用原 `PERFECT_CANDIDATE` 的全部硬门、#42/#44 参考距离门和质量阈值，没有为凑 8,000 放宽 A 级标准；
- 新 Binance 数据得到 966 个 A 级独立事件，加原 OKX A 级池 261 个；跨平台同事件去重、分布上限、时间切分与几何门后，得到 **1,043 个独立事件**；
- 1,043 个事件生成 **8,000 张像素唯一的 1280×742 无损 PNG**，其中 LONG 3,736、SHORT 4,264；
- 每张恰好一个 YOLO 框，框只覆盖冻结的 4/5 根核心 K；总检测窗仍为 18/19 根 K；
- 8 个不同前后文位置让框横向分布在 `x=0.3844…0.7883`，没有把所有框固定在同一位置；
- train 6,800 张 / val 1,200 张；对应 888 / 155 个独立事件，同一事件的全部 7–8 个变体强制只进入同一个 split；
- 独立校验重新解码并核对了全部 8,000 图、8,000 标签：尺寸、SHA、标签坐标、单框、零红框像素、事件分组全部通过；
- holdout OHLCV 读取为 0，没有启动训练，没有改负样本、模型、ACTIVE、forward、部署或下单状态。

最重要的口径：这是 **8,000 张训练输入图，但只有 1,043 个独立行情事件**。7–8 张图是同一 A 级事件在不同真实连续 K 线上下文中的位置变体，不是重复 PNG，也不能冒充 8,000 个独立事件。

另一个边界：`PERFECT_CANDIDATE` 是冻结规则自动筛出的 A 级候选，不是 Owner 逐张确认的 Gold；因此本轮仍标记 `training_eligible=false / production_eligible=false`。

入口：

- [8,000 张高清分页审核图库](../../experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/public/index.html)
- [实际训练 data.yaml](../../datasets/ma_launch_owner_grade_a8000_v1/data.yaml)
- 完整数据 manifest：`datasets/ma_launch_owner_grade_a8000_v1/manifest.jsonl`
- 构建摘要：`experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/summary.json`
- 独立 QA：`experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/qa/independent_qa_receipt.json`

![64 张分层抽样：8 个位置 × train/val × LONG/SHORT](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/qa/stratified_64_box_overlay.jpg)

## 数据扩容做了什么

数据来自 Binance 官方 `data.binance.vision` USD-M 月度归档。交易对范围是在保存的 exchangeInfo 快照中，`onboardDate < 2026-05-01` 的全部 USDT 永续；每个可用月度 ZIP 都先与官方 `.CHECKSUM` 做 SHA-256 对账，再合并为本地 15m 序列。

| 数据项 | 结果 |
|---|---:|
| 登记交易对 | 639 |
| 有完整历史输出 | 638 |
| 官方无数据 | 1 |
| 合并 K 线 | 49,162,351 |
| 校验通过月份 | 17,186 |
| 官方缺失月份 | 24 |
| 数据时间范围 | 2020-01-01…2026-04-30 |
| 归档硬截止 | `<2026-05-01T00:00:00Z` |
| 物化 holdout 行 | 0 |

拉取摘要 SHA256：`de0b6b64550d4a5cc7cd7f61a8f76750e3819bc8fb316d723c9a693befbd6546`。四个候选画像因为源窗口中存在非有限 OHLC/均线而被显式写入 `binance_profile_rejected.jsonl` 并淘汰；它们没有终止构建，也没有进入 A 级池。

## A 级规则没有放宽

本轮 hash-pinned 复用 `exp-15m-ma-launch-owner-perfect-filter10000-v1`：

| 轴 | 冻结要求 |
|---|---|
| 六均线密集 | 核心末六线宽度 `≤0.95 ATR`，核心包络 `≤1.50 ATR` |
| 边界新鲜 | 核心方向进度 `[-0.60, 1.00] ATR`，不能已走完大段 |
| 收拢/交叉 | 持续变窄或至少 3 次均线次序翻转 |
| 价格接触 | K 线触及均线束，收盘不能明显脱离 |
| 前置安静 | 核心前 12 根实体、路径、最近 3 根偷跑受限 |
| 历史释放 | 核心后 1/2/3/5 根沿方向干净释放 |
| 干净度 | 影线、反向实体、回撤受限 |
| 参考相似 | 通过 #42/#44 锚、语义反例和 v7 接受族的锁步 + DTW/DDTW 距离门 |

质量阈值仍为 `0.3611898959`；最终 8,000 张对应事件的质量分数最小/中位/最大为 `0.3763 / 0.5169 / 0.6569`，没有低于冻结阈值。SHORT 只做方向通道归一化，时间从不倒放。

历史释放使用核心之后 5 根 K，因此这是一套 completed-history 检索标签，不是 tip/forward 因果信号。它不能直接接实盘扫描。

## 完整筛选漏斗

| 阶段 | 数量 | 说明 |
|---|---:|---|
| Binance 原始宽候选 | 39,022 | 扫描日志中的 NMS 前候选 |
| 1 小时 NMS 后 | 31,389 | 同币同方向相邻候选合并 |
| 可计算严格画像 | 31,385 | 4 个非有限源窗口淘汰 |
| 全部绝对硬门通过 | 1,299 | 4.14% 的可评分候选 |
| Binance `PERFECT_CANDIDATE` | 966 | 硬门 + 参考距离 + 质量阈值 |
| 加入原 OKX A 级池 | 1,227 | 966 + 261 |
| 跨平台 4 小时事件去重 | 1,075 | 同币同方向同一行情只留质量更高者 |
| 币×方向×时间块分布上限后 | 1,064 | 每时间块最多 2、每币方向最多 12 |
| split / purge / 几何合格事件 | 1,043 | 每个事件至少 7 个合法短窗位置 |
| 最终图像 | 8,000 | 1,043 个事件的 7–8 个位置变体 |

最终覆盖 404 个规范化币种、499 个源文件。事件时间范围为 `2020-03-16` 至 `2026-05-03`；其中 Binance 源严格截止于 2026-04-30，最后三天只来自既有、已固定 SHA 的 OKX pre-holdout 源。

## 为什么不是一开始的 5–6 张/事件

第一次容量方案固定六个位置：`(PRE,POST)=(6,8)…(11,3)`，几何合格事件为 1,049；即使每个事件都用满 6 张，理论上限也只有 6,294，程序按约定 fail closed，没有拿 B 级、重复图或近邻滑窗补数。

第二次只改一个变量：把同样长度的真实连续 K 线上下文增加到八个位置：

`(5,9), (6,8), (7,7), (8,6), (9,5), (10,4), (11,3), (12,2)`。

不变项包括 A 级门、4/5 根核心边界、18/19 根总窗、1280×742 渲染器、跨平台去重、时间切分和 holdout 边界。容量预检得到 1,043 个事件：1,035 个拥有 8 个合法位置，8 个拥有 7 个位置，最大像素唯一容量 8,336。最终为精确 8,000 张，699 个事件分配 8 张、344 个事件分配 7 张。

这个修正记录在 `capacity_attempts.json`，没有把失败的第一轮覆盖掉。

## 图像、框和标签

| 项目 | 最终结果 |
|---|---:|
| 图像格式 | lossless PNG |
| 尺寸 | 8,000/8,000 为 1280×742 |
| 每图框数 | 恰好 1 |
| 核心宽度 | 4 根 4,346 张；5 根 3,654 张 |
| 总窗长度 | 18 根 4,346 张；19 根 3,654 张 |
| 框宽归一化 | 0.2117…0.2563 |
| 框中心 x | 0.3844…0.7883 |
| 框高归一化 | 0.0431…0.5485 |
| 模型 PNG 中红框像素 | 0 |
| 图像 SHA 唯一 | 8,000/8,000 |
| YOLO label | 8,000/8,000，逐项匹配 manifest |

训练 PNG 没有红框；审核网页和下面两张总览仅用 CSS 或临时预览副本叠框。框高最大的边界样本仍只覆盖原 4/5 根核心以及六均线，较高是因为首根启动 K 波动较大；本轮没有二次手缩框或改变语义边界。

![框高最大的 16 个独立事件边界复核](../experiments/active/exp-15m-ma-launch-owner-grade-a8000-v1/results/qa/largest_box_height_16.jpg)

8 个位置的图数分别为：997、1,005、1,006、1,002、999、997、1,005、989，没有单一位置占主导。全量审核图库共 80 页、8,000 个图像引用和 8,000 个 CSS 框，所有相对路径均存在。

## 时间切分

全局 cutoff 固定为 `2025-12-01T00:00:00Z`，切点两侧各 purge 150 根 15m K，并按最宽的“渲染窗口 + 核心后 5 根历史确认”依赖区间裁决。

| split | 独立事件 | 图片 | 实际窗口范围 |
|---|---:|---:|---|
| train | 888 | 6,800 | 2020-03-16…2025-11-28 |
| val | 155 | 1,200 | 2025-12-04…2026-05-03 |

同一 `sample_id` 的所有 7–8 个位置变体进入同一个 split；跨 split 事件为 0。这里采用时间切分，不是随机切分。

## 独立 QA 与零假设

本轮是标签/渲染数据工程，不定义入场、出场、TP/SL、成本或模型分数。因此 val AUC、top-decile 毛/净收益、胜率、单特征收益基线、匹配随机入场对照和收益置换检验均不适用；不能为了填表虚构交易指标。

独立校验器不调用构建器内部 QA，重新读取发布后的 8,000 图和标签：

| 检查 | 真实数据 | 1000 次固定随机零假设 |
|---|---:|---:|
| 标签与所属图片精确匹配 | 8,000/8,000 | 中位 1，范围 0–5 |
| 跨 train/val 的事件 | 0/1,043 | 中位 742，范围 709–773 |
| 单侧置换 p | 0.000999 | — |

此外全量重算确认：8,000 张 PNG 全部可解码、全部 SHA 与 manifest 相等、8,000 张图 SHA 唯一、8,000 个 label 与 class/box 坐标逐项相等、精确 overlay 红色像素为 0。

冻结 A 级过滤器此前的 1,000 张边界零假设仍由 hash-pinned 配置继承：正确边界 morphology-only 通过 5.1%，左移 3 根为 3.2%，右移 3 根为 0%，方向翻转为 0%。这只是规则辨别力证据，不是收益证据。

## 产物与哈希

| 产物 | SHA256 |
|---|---|
| 预注册/容量修订 | `6aed7e9b13a224e23724354e0d7e20b18ec543779a3824adbe5eb6aaa6513462` |
| 数据 manifest | `efe1eb97c14c6e94dff32bdeb2c420113890c378a6b808d94f5978a887848b40` |
| data.yaml | `eee91403b477a9faaf76c18a94bf7f61a787398b60cdd6a53580abe86722c9b5` |
| 构建 summary | `3a1124c00b93cdcd4ab1268652ef53bb8c08ff874b39f9c07d05bb4473578a92` |
| 独立 QA receipt | `b3661752831b0d11a6d3d10092126911901cc5bd67ad8abcf56db8a084bb99d1` |
| 视觉 QA receipt | `bf7419f9d419a0197fb7a54a8800706042b83b04ac94e5139e1d73dfe8df202c` |

数据集总大小约 408 MiB，其中无损 PNG 约 351 MiB。图片和本地 Binance 历史数据受 `.gitignore` 管理，不把大文件塞进 Git；注册表只保存身份、路径与 SHA。

## 风险与诚实声明

1. **8,000 张不等于 8,000 个独立事件。** 有效事件数是 1,043；后续训练评估必须继续按事件分组，不能把 7–8 个变体当作独立统计样本。
2. **自动 A 候选不是 Owner Gold。** 规则由 #42/#44、6 个语义反例和 v7 接受族定标，正锚仍然很少；全量真实纯度没有 Owner 逐张置信区间。
3. **包含未来确认。** 标签检索使用核心后 5 根，图窗最多显示 9 根 post 上下文；它只能做 completed-history 检测研究，不得冒充新鲜盘口信号或接 ACTIVE。
4. **跨交易所域差异。** 6,809 张来自 Binance、1,191 张来自 OKX；虽使用同一 OHLCV 渲染器和同一均线算法，微观价格路径仍有 venue 差异。
5. **高波动边界样本存在。** 最大框高 0.5485，仍低于冻结 0.55 图形门；如果 Owner 要进一步定义“A+ 极致纯净”，应作为新的单变量门实验，不应在本轮事后静默删除。
6. **负样本未改变。** 本轮只完成 Owner 当前要求的正样本扩容；不能把它与旧 30,000 负样本直接拼接后宣称已可训练。
7. **没有训练或 promote。** 当前项目仍处于 P0/P1，`training_eligible=false`，生产没有 ACTIVE bundle；本轮没有越过门禁。
8. **holdout 未消费。** 所有新 Binance 数据严格 `<2026-05-01`，既有 OKX源严格 `<2026-05-04`；物化 holdout 行为 0。

## 复现命令

```bash
# 1) 官方 Binance USD-M 15m 归档；支持断点续传并逐月校验 CHECKSUM
PYTHONPATH=. python3 scripts/fetch_binance_um_preholdout_15m.py --workers 32

# 2) 从零扫描、严格评分、跨源去重、时间切分、渲染 8000 图与标签
# 默认拒绝覆盖既有 results/dataset；在干净副本或移走旧产物后执行
PYTHONPATH=. python3 scripts/build_15m_ma_launch_owner_grade_a8000.py

# 3) 实际发布数据的全量独立 QA + 1000 次零假设置换
PYTHONPATH=. python3 scripts/verify_15m_ma_launch_owner_grade_a8000.py

# 4) 64 张分层视觉 QA 与最大框高边界 QA
PYTHONPATH=. python3 scripts/render_15m_ma_launch_owner_grade_a8000_qa.py

# 5) 定向/相邻回归
PYTHONPATH=. python3 -m pytest -q \
  tests/test_ma_launch_owner_grade_a8000.py \
  tests/test_binance_um_archives.py \
  tests/test_ma_launch_owner_perfect_filter.py \
  tests/test_ma_launch_owner_autofill10000.py

# 6) 报告 HTML
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_owner_grade_a8000_20260828.md \
  --out-dir analysis/html
```

数据集 builder commit：`b9fc59488c98552cfa26537ccf22980e6a7c3678`；独立 QA commit：`26917a1cc5ed272d6be8c0fb316d63bdd2c77ffe`。

## 下一步

本轮到此交付的是“8,000 张 A 级自动候选正样本 + 标签 + 全量图库 + 独立 QA”，没有自动开训。若 Owner 下一步确认要把它升级为训练集，应另行冻结：Owner Gold 抽验口径、负样本数量与保护区、event-group 数据切分以及是否允许 completed-history 检测架构；这些决策不能由本轮正样本数量自动推出。
