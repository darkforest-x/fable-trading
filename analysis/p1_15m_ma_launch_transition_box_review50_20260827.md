# P1：15m 启动源区两段式框 Review50（2026-08-27）

## 结论先行

Owner 的三张红框证明，正确目标不是上一版的“六均线细包络”，也不是“把横向范围内全部 K 线做外接矩形”。红框表达的是一个**两段式对象**：启动前核心区决定纵向价格带，随后几根启动确认 K 只延长横向证据；确认大 K 可以从框顶/框底穿出。

已按这一语义重做独立 v2 Review50，不覆盖旧产物：核心固定结束于候选锚点 `t-3`，候选核心 4/5/6/7 根，横向统一再带 3 根确认 K 到 `t`，因此总宽 7/8/9/10 根；纵向只取核心 full-wick 与 SMA/EMA 20/60/120 的并集，再加上下各 4% 价格跨度。旧 L5/min24 MA-only 框保留为青色虚线，新框为红色，黄色竖线区分核心与确认段。

50/50 张底图 SHA 与 v1 完全相同，200/200 个候选框都完整包含各自核心的影线和六均线；把确认段 high/low/六均线做大幅干预后，200/200 个框的纵向坐标变化严格为 0。没有生成 YOLO 标签、没有重做训练集、没有启动 3060，holdout OHLCV 读取 0。

当前唯一审核入口：`experiments/active/exp-15m-ma-launch-transition-box-review50-v2/results/public/index.html`。

## Owner 样例告诉了我们什么

对三张原始 2940×1696 截图的红色长直线做像素测量，矩形约为：

| 样例 | 红框约 XYXY | 横向 K 数估计 | 关键观察 |
|---|---:|---:|---|
| BTC 向上 2026-08-19 | `[1671,1148,1745,1268]` | 7–8 | 首根扩张大 K 穿出框顶，不能参与纵向上界 |
| BTC 向下 2026-08-10/11 | `[1159,410,1221,546]` | 6–7 | 后续下跌穿出框底，不能把完整跌幅包进框 |
| FARTCOIN 向上 2026-08-19/20 | `[1951,1256,2052,1386]` | 9–10 | 红框保留早期启动横向过程，但后段拉升离开价格区 |

这些只是 Owner 提供的**协议设计参考截图**。本轮没有反查截图对应 OHLCV，没有把 2026-08 的像素当评估样本，也没有让它们进入训练输入或标签，所以不构成一次 holdout 消耗。

## 新旧框同图对照

下图每格底图相同：红色为 v2，青色虚线为被 Owner 否决的 v1 L5/min24，黄色竖线右侧固定为 3 根确认 K。

![LONG 四档对照](../experiments/active/exp-15m-ma-launch-transition-box-review50-v2/results/public/images/346f01e4ae63623fe488123f_comparison.png)

![SHORT 四档对照](../experiments/active/exp-15m-ma-launch-transition-box-review50-v2/results/public/images/02a6efffa32fc2ab8f2313c7_comparison.png)

## 数据与身份

| 项目 | 数值 |
|---|---:|
| v2 审核图 | 50 |
| LONG / SHORT | 25 / 25 |
| train / val 身份 | 40 / 10 |
| 时间范围 | 2022-01-14 13:00 UTC ～ 2026-05-02 14:00 UTC |
| 唯一 sample_id | 50 |
| 与 v1 clean PNG SHA 一致 | 50 / 50 |
| 每张候选框 | 4 |
| 候选框总数 | 200 |
| 默认答案 | 0 |
| holdout OHLCV 物化 | 0 行 |
| 新 YOLO label / 训练图 / 训练 | 0 / 0 / 0 |

本轮抽的是冻结弱正例 Review50，所以“正类率”没有统计意义；它不是从正负混合总体随机抽出的分类验证集。v1 已经审计的 26,874 个空标签负样本也没有被悄悄复用或改标。本轮问题只是在确认正框语义；若通过，hard negative 与 Gold 禁入区必须按新框重新审计。

## 几何结果：相对上一版不再是细框

以下尺寸均是在 `imgsz=960`、保持 1280×742 原图纵横比时的模型输入像素。v1 列是同一批 50 张的旧 L5/min24。

| 方案 | 总宽 K 数 | 宽度中位 px | 相对 v1 宽度中位倍数 | 高度中位 px | 相对 v1 高度逐样本中位倍数 | 至少一项确认极值穿出框的样本 |
|---|---:|---:|---:|---:|---:|---:|
| v1 L5/min24（否决） | 5 | 248.3 | 1.00× | 52.1 | 1.00× | 不适用 |
| Core4 + Confirm3 | 7 | 331.5 | 1.335× | 171.0 | 3.203× | 38 / 50 |
| Core5 + Confirm3 | 8 | 381.0 | 1.534× | 172.1 | 3.351× | 38 / 50 |
| Core6 + Confirm3 | 9 | 430.5 | 1.734× | 191.6 | 3.374× | 38 / 50 |
| Core7 + Confirm3 | 10 | 480.8 | 1.936× | 192.0 | 3.395× | 38 / 50 |

“确认极值穿出框”不是失败，反而是两段式语义在真实样本上的直接证据：以 Core5 为例，50 张共 150 根确认 K、300 个 high/low 极值，其中 83 个落在纵向源区之外；如果把这些确认极值并进 y 轴，框就会被启动行情拉成巨框。

高度没有强行统一成同一个像素值。统一的是**生成规则**，不是物体天然大小：不同核心的实际价格跨度必须保留，否则模型会学到人工固定框模板。当前 Core4 的模型高度范围仍达到 40.5–466.5px，说明固定 `t-3` 并不保证每张核心边界都正确；这正是 v2 只能进入人工 Review、不能直接批量转标签的原因。

## 非方向性零假设对照

本轮是标签几何审计，没有交易方向、收益、AUC、top-decile、胜率或 matched-random 入场可计算；编造这些指标会混淆问题。等价的零假设是：**若实现仍偷偷把确认段用于纵向定框，那么只改变确认段数值应改变 y 坐标。**

实际对每张图的 4 个候选都做了固定渲染 transform 的强干预：确认段 `high×1.70`、`low×0.60`、六均线 `×1.40`，核心数据不变。200/200 个候选的 `y0/y1/core_price_high/core_price_low/box_price_high/box_price_low` 最大绝对变化均为 `0.0`；同时 200/200 个原框完整包含核心影线与六均线。这同时拒绝“确认段污染 y 轴”和“核心对象漏出框”的两种实现错误。

## 为什么上一版错

上一版把目标定义成“在 `t-12..t-1` 里另找最密 4–7 根，只包六均线”。它有三处语义偏移：

1. 忘掉了 Owner 已要求的 `t-3` 边界，横向段可以跑到更早位置；
2. 把启动源区缩成数学意义上的六条线，K 线与均线共同形成的价格区消失；
3. 核心框没有横向保留启动确认过程，因此看起来明显过窄。

简单增加 16/24/32px 最小高度只是在错误对象上加粗，不能修正上述三点。反过来，把确认大 K 的 high/low 全并入又会把框拉成整段涨跌幅；正确修复必须把 x/y 的 bar span 分开。

## 风险与诚实声明

- v2 是**协议候选**，不是 50 张 Gold，更不是 9,938 张 Gold。Owner 认可方向不等于确认每张 START/END。
- `t-3` 是现有弱标签锚点，不是逐图识别出的真实启动首根。若一张红框明显偏左/偏右，后续必须在带编号图上逐张选择核心边界，禁止统一 delta 或哈希随机长度。
- Core4–7 的纵向跨度仍可能很大；极端影线比达到 8 会 fail-closed 为 IGNORE，但这不等价于所有非极端样本都合格。
- 新正框会改变 Gold 禁入区和 hard-negative 冲突关系。未完成重新联结前，旧负样本只能保持旧数据版本，不能被称为“已同步修好”。
- 固定 W20 的模型视图与 TradingView 数百根全景在像素宽度上不可直接比较；本轮比较的是 bar 语义与同一 W20 内的新旧框，不拿全景像素比例冒充训练尺寸结论。
- 生产仍无 active bundle，P0/P1 门未通过；不训练、不 promote、不部署、不改 forward/交易状态。

## 下一步（需要 Owner 先看图）

1. 在 v2 页面抽看 LONG/SHORT，判断红框是否终于接近三张参考；每张可选 Core4–7 或“仍需调整”。
2. 若两段式语义通过，下一步不是直接全量生成标签，而是给样本 K 线编号，逐图确定核心 START；核心 END 也要按真实启动首根验证，不能假设全部都是 `t-3`。
3. 逐样本边界闭合后，再按新框重建 Gold 禁入区、重新取 easy/hard negatives，并做时间切分与正负渲染 parity 审计。
4. 只有新 Gold 数据集的类别、框、负样本和 split 全通过，且 Owner 另行批准 `training_eligible=true` 后，才讨论 3060 训练。

## 复现命令

构建器和协议必须先提交；官方产物拒绝覆盖已有目录。

```bash
cd /Users/zhangzc/fable-trading
git branch --show-current
PYTHONPATH=. python3 -m pytest -q \
  tests/test_ma_launch_transition_box_review.py \
  tests/test_ma_launch_ma_box_review.py \
  tests/causality/test_gold_annotation_contract.py
PYTHONPATH=. python3 scripts/build_15m_ma_launch_transition_box_review50.py
python3 scripts/md_to_html.py \
  analysis/p1_15m_ma_launch_transition_box_review50_20260827.md \
  --out-dir analysis/html
```

关键冻结身份：

```text
builder commit: 46dcfa857b36ae9a617faf8566c30f8171f71c06
prereg SHA256: aec480080bc4ff229ebbc652423071fa65e67b949a8e8a3fc95436b0783014b5
v1 source manifest SHA256: cc852cb9da838056a8c95e80ba60270fa1537860973437b35638cff1efe63c66
v2 review manifest SHA256: 9b537a0af28a0c4602340bea5d759ab5869c759d4d49d4b5a00b4a161df4db53
v2 review HTML SHA256: 9b6dbd03525ca82b481a40d455a03d7d383efcd2bf91d51ba9f8a4fe4a00d731
```
