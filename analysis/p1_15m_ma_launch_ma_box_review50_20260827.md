# 15m 六均线密集框协议 Review50：固定 W20、模型像素下限与负样本冲突审计

## 结论先行

本轮已把“框住 K 线”改成一套**只由六条均线决定的候选框协议**，并完成全量数据核算与 50 张分层审核包；
但它仍是协议 Review50，不是 Gold 数据集，也没有启动训练。

- 输入图固定为原生 **1280×742、W20**，没有离线缩图、压缩另存或改变 K 线/均线颜色。
- 横向不再按哈希随便分 4–7 根并统一卡在 `t-3`；每个 L4/L5/L6/L7 都只在
  `t-12..t-1` 内独立寻找六均线包络最紧的连续段。
- 纵向完全不读取 K 线 `high/low` 定边界，只包住该段的 SMA/EMA 20/60/120，再加每侧 4 个源图像素。
- 为避免青框过薄，Review50 同图比较模型实际 `imgsz=960` 尺度下的最小 **16/24/32px**；
  当前建议候选为 **L5 + min24**，但页面没有预选答案。
- 9,938/9,938 个正例的 L5/min24 框都完整包含六条均线；模型输入框高最小 24px、中位 53.25px、
  p90 96.75px，横向约 248px。也就是说，极窄小目标被下限挡住，但真实均线发散较大时仍会诚实变高。
- 框中心不是一个固定坐标：全量 L5 候选覆盖图宽的 0.370–0.835，middle 7,400、right 2,538。
  没有强行造 left 样本，因为把核心推到更左通常需要加入更多核心之后 K 线；那会改变延迟与因果契约。
- 旧负样本 26,874 个全部得到核算：26,802 个有完整六均线/W20，可比较；72 个在 120 均线预热前或
  源文件边界处，明确记为不可定义，没有补值、换样或静默删除。
- hard 负样本有明显语义重叠：**29.04%** 的 hard negative 六均线包络不宽于正样本中位数；
  easy negative 只有 **0.22%**。这说明若 L1 的类定义只是“均线密集结”，旧 hard 空标签会产生冲突；
  若类定义是“即将启动的均线结”，就必须明确加入启动/释放语义，不能只靠框几何。

Owner 可直接逐张审核 50 个样本：

[打开六均线框 Review50](../../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/public/index.html)

页面每次只显示一张底图：第一行比较 L4/L5/L6/L7，第二行固定 L5 比较 exact/16/24/32px；
接受一张前必须同时选择横向长度和最小高度。页面只导出审核 JSON，不会写训练标签或启动训练。

## 这次具体修了什么

| 项目 | 旧 9,938 张训练标签 | 本轮 Review50 候选 |
|---|---|---|
| 输入宽度 | W14–22 混合 | 固定 W20 |
| 横向来源 | 哈希分配 4–7 根，统一结束 `t-3` | 在 `t-12..t-1` 内由六均线密集度独立选 L4/L5/L6/L7 |
| 纵向来源 | 核心 K 的最高价到最低价 | 只取六条均线 min/max |
| 留白 | 围绕 K 线极值 | 每侧 4 个源图像素，再应用模型尺度最小高度 |
| 小目标控制 | 没有按模型输入像素定义 | 对比 16/24/32px，明确列出 `scale=0.9` 后尺寸 |
| 框位置 | 结束位置固定，容易形成位置捷径 | 数据决定密集段；全量中心覆盖 0.370–0.835 |
| 当前资格 | 已训练，但目标语义错误，非 Gold | 只供协议审核；0 label、0 training image、0 model |

旧审计中，现有框与局部六均线结二维 IoU 中位数只有 0.0215，且 21.90% 与同横向均线包络纵向完全不相交。
新候选不靠提高分辨率掩盖这个问题，而是从原始 OHLCV 重新计算均线边界；9,938/9,938 个有效正例均满足
“六条均线全部在框内”。

## 正例渲染结果

下图每一行是同一张 W20 原图，四列只改变横向 L；所有横向对比都使用 min24，蓝框只跟着均线带。

![LONG 横向 L4-L7 对比](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/horizontal_long_overview.png)

![SHORT 横向 L4-L7 对比](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/horizontal_short_overview.png)

第二组固定 L5，只比较纵向 exact+4px、min16、min24、min32。若原始均线包络已经高于某个下限，
几个框完全重合是正确行为；下限只负责托住最薄的框，不会把所有样本强制成同一高度。

![LONG 纵向最小高度对比](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/vertical_long_overview.png)

![SHORT 纵向最小高度对比](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/vertical_short_overview.png)

### 为什么高度仍然不完全一样

“统一”应当统一**生成规则和模型尺度下限**，不应把所有框硬改成同一个像素高度：

1. 六条均线真实发散程度不同，强行同高会在宽带样本上切掉目标，或在窄带样本上塞入大量无关背景。
2. 每张图的价格纵轴由本图 W20 决定，同样的价格跨度映射到像素后本来就会不同。
3. min24 的作用是保证下界，不是抹平真实尺寸。全量 L5/min24 的模型输入高度为：

| 指标 | 高度（imgsz=960 输入像素） |
|---|---:|
| 最小 | 24.00 |
| p10 | 26.25 |
| 中位数 | 53.25 |
| p90 | 96.75 |
| 最大 | 196.50 |
| `scale=0.9` 后最小 | 21.60 |

在 50 张分层样本里，exact 最低 18.75px；min16 没有改变任何一张，min24 只托高 4/50，min32 托高 7/50。
所以 16px 对这批图几乎没有保护作用，32px 又更容易加入背景，**min24 是合理的审核起点**，不是已确认定案。

横向如果最终选固定 L5，模型输入宽度为 247.58–248.33px；即使 `scale=0.9` 仍约 223px，
横向不是小目标。真正需要保护的是纵向高度。

## 框位置分布

固定 W20 后，L5 宽度近似固定，但密集段起止由数据决定，中心不是按样本 ID 配一个假位置：

| L5/min24 项目 | 结果 |
|---|---:|
| 中心最小 / 中位 / 最大 | 0.370 / 0.525 / 0.835 |
| middle | 7,400 |
| right | 2,538 |
| left | 0 |

没有 left 不是遗漏。密集搜索严格位于 `t-12..t-1`，而 W20 还要保留 t 附近的启动上下文，所以候选自然位于中右侧。
若为了凑三段位置把框推到左侧，必须加入更多核心之后的 K 线或造空白边；前者增加未来可见度与信号延迟，
后者制造训练/检测域差异。本轮拒绝为了位置直方图好看而做这两件事。

Review50 的底图结束位置以稳定哈希均分到 `t/t+1/t+2`，只用于让 Owner 检查不同显示位置与缩放下的几何稳健性；
它**没有资格原样进入新鲜实盘训练**。若以后接入实盘，必须冻结一个因果右端，并把输出时刻记为完整检测窗右端，
遵守 tip/tip-1/tip-2 纪律。

![框尺寸、负样本重叠与位置分布](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/box_and_negative_distributions.png)

## 负样本结果：旧 hard negative 不能不审就复用

| 样本组 | 可计算数 | L5 六均线包络/均价中位数 | p90 | 不宽于正例中位数的比例 |
|---|---:|---:|---:|---:|
| 正例 | 9,938 | 0.644% | 1.413% | 50.00%（定义） |
| hard negative | 16,886 | 0.850% | 1.296% | **29.04%** |
| easy negative | 9,916 | 2.504% | 5.835% | **0.22%** |

数值越低表示六均线越密。easy negative 与正例分离明显；hard negative 却有大面积重叠。
下面橙框是负样本上的**反事实 L5/min24**，仅用于显示“这里若按纯均线密集定义会框到哪里”，不是标签：

![最密 hard negative 反事实框](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/public/images/negative/neg_hard_51e83b999ea03b94aa267dbd.png)

![中位 hard negative 反事实框](../experiments/active/exp-15m-ma-launch-ma-box-review50-v1/results/public/images/negative/neg_hard_2ee6e0a10d6615f491f18bda.png)

因此下一版负样本不能机械沿用：

- 若 L1 定义为“定位任何六均线结”，这些密集 hard negatives 不应是空标签，启动与否应由 L2 判断。
- 若 L1 定义为“定位即将启动的六均线结”，hard negatives 可以为空，但 Gold 协议必须写出启动/释放条件，
  并证明条件在检测窗右端之前可见；不能让未来结果偷偷成为输入特征。
- 在 Owner 选定这两个定义之一前，全量旧 hard negatives 保持冻结，不自动改正、不自动删负样本。

### 72 个不可定义负样本

全部 26,874 个负样本都有账：70 个发生在 SMA120 尚未预热完整的位置，2 个固定 W20 会越过源文件边界。
本轮将它们写入 `unavailable_negative_audit.jsonl`，不补均线、不缩 W20、不找替身；因此密集度结果的有效分母是
26,802，而身份核算仍是 26,874/26,874。

## Review50 覆盖与数据范围

| 项目 | 结果 |
|---|---:|
| 冻结 manifest 总数 | 36,812 |
| 正例 / 负例 | 9,938 / 26,874 |
| 正类率 | 26.997% |
| 正例时间 | 2022-01-05 17:30Z ～ 2026-05-03 12:00Z |
| 负例时间 | 2022-01-03 23:30Z ～ 2026-05-03 17:30Z |
| OHLCV 源文件 | 228 |
| 物化 pre-holdout OHLCV | 10,139,450 行 |
| 物化 holdout OHLCV | 0 行 |
| Review50 LONG / SHORT | 25 / 25 |
| Review50 train / val | 40 / 10 |
| 时间分层 | 5 桶，每桶 10 张 |
| Review50 唯一币种 | 49 |
| 审核答案预选 | 0 |

源 manifest SHA-256：
`dd55246938b03c4b2013d159cdfee94b4e9db56ecb298ad30145eb5d1bc2bc3a`。

## 零假设与质量对照

这是非方向性的标签/渲染协议审计，没有收益标签，所以 val AUC、收益置换检验、top-decile 毛/净收益、胜率、
单特征收益基线和匹配随机入场对照都不适用；本报告不编造这些指标。

等价的严格零假设是：“框仍可能依赖 K 线极值或 t 之后数据，只是截图看起来像均线框”。本轮用三道反证：

| 对照 | 结果 |
|---|---:|
| 将候选段 K 线 high/low 人为扩大 ±1000，保持同一渲染变换 | 框坐标逐值不变 |
| 将 t 及之后 OHLC/MA 放大 50 倍 | `t-12..t-1` 密集段选择逐值不变 |
| 全量有效正/负样本 L5/min24 | 六条均线在框内 36,740 / 36,740 |

另外，8 个聚焦测试全部通过；HTML 的 JavaScript 语法、50 个身份、174 张引用图、所有图像哈希、
导出完整性门和“接受前必须选 L/高度”均通过静态检查。

真实浏览器点击验收没有伪造为通过：Codex in-app Browser 的 `file://` 安全策略拒绝导航，本轮没有用另一浏览器或
localhost 绕过。静态验收回执明确记录 `browser_interaction.completed=false`；Owner 可直接打开上面的 HTML 实际操作。

## 复现命令

初版构建器先于首次运行入库，commit 为 `7bda8fb32d98f0ed5f3aaf1b71d620183bb385dd`；第一次 fail-closed
发现 72 个负样本边界后，修订说明与处理代码在
`d6460b9ee4e0a8b181ea4d076c06cbb8abfc8b89` 入库，再从零正式构建。

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  yoyo/datasets/ma_launch_ma_box_review.py \
  scripts/build_15m_ma_launch_ma_box_review50.py \
  scripts/summarize_15m_ma_launch_ma_box_review50.py

PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_ma_launch_ma_box_review.py

# 在正式 results 不存在的干净复现环境中执行；构建器拒绝覆盖已有产物。
PYTHONPATH=. .venv/bin/python \
  scripts/build_15m_ma_launch_ma_box_review50.py

python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_ma_box_review50_20260827.md \
  --out-dir analysis/html
```

Owner 审完并从页面导出 JSON 后，接回命令为：

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/summarize_15m_ma_launch_ma_box_review50.py \
  /path/to/exported_answers.json
```

该脚本会逐张核对 sample/symbol/direction/time/image SHA；即使 50/50 全部审核完成，输出仍保持
`sample_owner_confirmed=false / training_eligible=false`，不会把协议确认偷换成全量 Gold。

## 风险与诚实声明

- 本轮没有生成任何 YOLO `.txt`、训练图、权重或评估分数；没有在 3060 上训练。
- Review50 是协议选择小样，页面中的框全是候选。Owner 认可一个全局 L/高度，不等于确认其余 9,888 张正例。
- L5/min24 是基于尺寸保护与 50 张预览提出的起点，不是统计证明的最优超参数；不同 L/高度的真正优劣必须先由
  Owner 视觉定义，再做单变量模型实验。
- `scale=0.9` 会把 baseline min24 缩到 21.6px。若 Owner 要求“任何增强后仍 ≥24px”，后续需单独冻结
  baseline 26.67px 下限或关闭 scale；这属于下一轮单变量，不能在本轮暗改。
- Review50 为位置稳健性展示了 t/t+1/t+2；任何包含核心之后 K 线的模型不得冒充新鲜盘口检测器。
- 负样本重叠只证明“密集度单变量不够”和“类定义必须明确”，不等于 29.04% hard negatives 都应转正。
- holdout 读取 0；ACTIVE、frozen、forward、部署、仓位与下单状态均未改变。
- 旧 960/1280 弱标签模型和数据全部保留作错误基线，没有覆盖或删除。

## 当前门与下一步

本轮可交付状态是：`review50_ready_pending_owner_protocol_and_sample_confirmation`。

1. Owner 在 Review50 逐张选择 L4/L5/L6/L7、16/24/32px，并作 ACCEPT/ADJUST/UNCERTAIN。
2. 根据导出 JSON，只冻结一个全局框协议，同时明确 L1 是“任何密集结”还是“即将启动的密集结”。
3. 再做逐样本 Gold：从原 OHLCV 生成候选框，Owner/双人复核不合格样本；旧 hard negatives 同步分类或设置 ignore，
   不能只改正例。
4. Gold 与负样本门通过后，才构建新版本 YOLO 数据集、做训练/检测渲染 parity，再单变量训练；训练前仍需 Owner
   明确批准 `training_eligible=true`。

在第 1–3 步完成前直接重训，会重复“把算法候选冒充人工 Gold”的老问题，因此本轮按项目 P0/P1 门停在可审核产物，
没有擅自启动 3060。
