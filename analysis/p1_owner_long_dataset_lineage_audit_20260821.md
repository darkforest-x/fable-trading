# P1 Owner-long 做多检测器数据谱系与镜像方案审计

日期：2026-08-21

## 结论先行

1. **BTC 与 ETH 都是 46 个事件只是去重后的计数巧合，不是每币限额。** BTC 原始检测
   1,117 条，ETH 原始检测 991 条；按同币核心中点 ±5 根聚合后，各自成为 46 个事件。
   两者只有 10 个 decision time 重合，每日分布不同；ADA 也恰好是 46。完整扫描的
   `_SUCCESS.json`、`scan_summary.json` 与两份 JSONL 数量闭合为 10,310 raw / 437 events。
2. Owner 对“0.8 以上才稍微像样”的观察与现有分布相符：BTC 46 个事件中仅 7 个
   `conf>=0.8`，ETH 仅 5 个；Core10 合计 80/437（18.31%）。但这是已消费 holdout use #1
   后的事后观察，只能把 0.8 登记成下一段新数据的候选阈值，不能在这同一批数据上再调完后
   宣称无偏 precision。
3. **可以按 R1 的结构做独立做多检测器，但不能复用 R1 的 916 个空标签文件。** 那 916 张在
   short-only R1 里是“Owner 明确判 long，所以不要报 short”的空标签 hard negative。做多集必须
   回到完整 1,152 个原始 `Owner-long` 框与已核对 OHLC，从原框重新生成正标签。
4. 长方向母池一对一谱系闭合：1,152 行去掉 8 个重复目标后为 1,144 个；按同币重叠窗口形成
   依赖块、最后 15% 时间块做 val、150 根 purge，得到 **963 train / 171 val / 10 drop**，
   train 截止 `2026-03-28T08:00Z`，val 最早 `2026-03-29T23:30Z`，实际隔离 158 根，
   跨 split 依赖 0，触碰 `2026-05-04` holdout 0。
5. 做多 R1 镜像 hard-negative 方案在未过滤母池上可闭合：1,210 个安全 Owner-short 方向反类，
   再按 W12–19 从 train 时间块模型排序补 716 个背景，达到 963 个 train positive 对应
   1,926 个 hard negatives。**最终数量必须在 Owner 过滤完成后重算，当前数字不能直接冻结开训。**
6. 现有六均线绳结 A/B/C 只能排审核顺序：long 为 A=343、B=482、C=327。独立 390 条
   keep/drop 反证为 AUC 0.489、置换 `p=0.635`，不支持代码自动 keep/remove。因此 Owner 的
   “A 档基本都能保留”是重要口径，但“基本”仍含例外，不能自动写成 343/343 全保留。

## 1. BTC/ETH 为什么刚好都是 46

扫描没有 `max_events_per_symbol`、`head(46)` 或图表数量截断。事件形成过程是：

1. 每个币 1,440 个 15 分钟 endpoint；每个 endpoint 渲染 W12–19 共 8 个因果窗口；
2. `conf>=0.25` 的预测框全部写入 `raw_detections.jsonl`；
3. 仅在同一 symbol 内，按预测核心中点相差不超过 5 根合并；
4. 第一次 crossing 固定为因果 decision，簇内最大 conf 只保存为审核统计，不把决策时间往后移；
5. 全部事件写入 `events.jsonl`，之后生成图，不存在按图数反向截断事件。

| 项目 | BTC | ETH | 说明 |
|---|---:|---:|---|
| raw detections | 1,117 | 991 | 已不同 |
| deduplicated events | 46 | 46 | 巧合相同 |
| `conf>=0.8` | 7 | 5 | 已不同 |
| `0.5<=conf<0.8` | 21 | 20 | 已不同 |
| `0.25<=conf<0.5` | 18 | 21 | 已不同 |
| 与另一币重合 decision time | 10 | 10 | 其余 36 个时刻不同 |

此外 ADA 为 1,156 raw → 46 events；其他币并非 46（XRP 55、BNB 53、DOGE 50、
AVAX 38、SOL 37、DOT 36、LINK 30）。这组横截面排除了“每币上限 46”的解释。

## 2. 当前 short R1 数据到底怎么来的

方向事实源是 `analysis/output/owner_side_review/review_sheet.csv`，SHA256：
`bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a`。

| Owner 逐框方向 | 原始行数 |
|---|---:|
| short | 1,361 |
| long | 1,152 |
| skip | 12 |
| 合计 | 2,525 |

short baseline 从 1,361 个 Owner-short 原框机械取中心半段（限制 4–7 根），15 个重复别名合并，
再丢 1 个 purge 行，实际正例为 1,143 train + 202 val。每个正例配 1 个同币、同 split、同 W、
避开全部 Owner 框 ±12 根的 easy negative。

R1 只改变 train hard negatives：

| R1 train 构成 | 数量 | 标签语义 |
|---|---:|---|
| short positive | 1,143 | 有 YOLO 正框 |
| easy background | 1,143 | 空标签 |
| Owner-long hard negative | 916 | 空标签；只表示“不是 short” |
| model-ranked safe background | 1,370 | 空标签 |

因此 916 张 `owner_long_manifest.jsonl` 虽保留了原框派生出的 core 起止位置，实际 `.txt` 标签 SHA
都是空文件 SHA `e3b0c442...`。直接复制或改文件名不会产生做多正框，必须重渲染。

## 3. 做多正例母池的独立干跑

Owner sheet 与绳结评分表按 `box_id` 2,525/2,525 一对一闭合；方向、decision time、
decision index、已解析 source path 的 mismatch 均为 0。旧文件名后缀虽过期，已用
symbol + index + timestamp 精确找回，不按过期文件名丢样本。

| 做多正例门 | 结果 |
|---|---:|
| Owner-long 原始行 | 1,152 |
| 重复目标组 / 删除别名 | 8 / 8 |
| 唯一正目标 | 1,144 |
| train / val / purge drop | 963 / 171 / 10 |
| 依赖块 | 1,097 |
| train→val 实际隔离 | 158 根 |
| 跨 split dependency | 0 |
| 完整可见窗触碰 holdout | 0 |

### 正例 W 分布

| W | train | val |
|---:|---:|---:|
| 12 | 92 | 13 |
| 13 | 205 | 29 |
| 14 | 227 | 25 |
| 15 | 127 | 38 |
| 16 | 171 | 41 |
| 17 | 84 | 21 |
| 18 | 27 | 0 |
| 19 | 30 | 4 |

保留行的核心宽度为 4/5/6/7 根 = 105/234/252/543。

### 诚实的确认层级

- `owner_side=long`：Owner 逐框方向确认，可作为 long 类别事实；
- 原始 Owner 大框：有原 Label Studio/方向表坐标谱系；
- 4–7 根橙色核心：由“原框中心半段”合同机械派生，**没有逐样本确认每个核心起止点**；
- 六均线 A/B/C：仅审核排序，不是标签；
- 最近 15 天 R1 高置信预测：是已消费 holdout 上的模型候选，不是新做多金标。

## 4. 做多 hard-negative 镜像方案

若最终保留母池仍是上面的 963 个 train positive，严格镜像 short R1：

| W | hard 目标（2×正例） | 安全 Owner-short | 需模型背景补足 |
|---:|---:|---:|---:|
| 12 | 184 | 183 | 1 |
| 13 | 410 | 306 | 104 |
| 14 | 454 | 246 | 208 |
| 15 | 254 | 116 | 138 |
| 16 | 342 | 214 | 128 |
| 17 | 168 | 77 | 91 |
| 18 | 54 | 22 | 32 |
| 19 | 60 | 46 | 14 |
| 合计 | 1,926 | 1,210 | 716 |

Owner-short 候选的排除为：晚于 long-train 截止 117、long 训练币种外 20、重复目标 13、
碰到 long 保护区 1。背景候选仍必须只来自 train 时间范围，避开所有 Owner 多空框 ±12 根；
模型分数只用于同 W 内排序，不选阈值、不看未来收益、不改 val。

## 5. 推荐的实际执行顺序

1. **先完成正例过滤，不训练。** 现有 2,525 页面已增加方向筛选，并默认 `A + long`，当前只需看
   343 张；快捷键仍为 K 保留、X 去掉、? 待定。A 不是自动保留门，最终必须有逐条 export，
   或 Owner 明确给出无例外的批量规则。
2. **从 export 生成新的 long candidate manifest。** 回到原 Owner-long box + 精确 OHLC 源，
   重建 4–7 根正框；不得读取 R1 空标签作为正例，不覆盖任何旧 dataset。
3. **过滤后重新做依赖去重和时间切分。** 先确定最终正例，再按同 symbol 重叠窗口成组；禁止先切
   split 后删图，也禁止随机切分。
4. **重建 easy/hard negative。** easy 1:1；Owner-short 方向反类优先；不足部分才由初始 long
   baseline 在 train 背景池内排序挖取。所有 W 数量在最终正例 W 分布上重算。
5. **生成训练前盲审包与新版本 SHA。** 主审图只含 decision 时可见内容；未来参考物理分目录；
   manifest 记录 image/label/source SHA、Owner box id、方向确认层级、core 派生规则、dependency id。
   `training_eligible=false` 保持不变。
6. **Owner 明确批准后才训练。** P0/P1 门通过后，训练配置镜像 short R1，只把目标方向换成 long；
   不在同轮改变 renderer、窗口、增强、阈值或模型底座。当前不用 3060，也未启动 Mac 训练。
7. **0.8 只预注册到新验证块。** 下一段未见连续块先冻结 `conf=0.8`、最大事件密度与 semantic
   precision 门；不能回到本次 15 天图上继续调到满意再报成绩。

## 6. 非方向性审计的零假设对照

本报告是数据谱系/切分审计，不适用 AUC、top-decile 收益、成本后净收益、匹配随机交易对照。
同等严格的零假设证据采用已冻结的 390 条 Owner keep/drop 独立反证：若绳结分与 Owner 保留裁决
无关，标签可交换。实测 AUC 0.489、keep-drop 均值差 -0.0064、10,000 次置换 `p=0.635`，
没有拒绝零假设。因此本报告明确禁止把 A/B/C 自动转成训练标签。

## 7. 风险与诚实声明

- `conf>=0.8` 的视觉判断来自已看过的 holdout use #1；可形成新假设，不能形成同批无偏验收。
- long 方向标签是 Owner 事实，但中心 4–7 根仍是合同派生，不等于逐 bar 金标。
- 上述 963/171、1,210/716 都是**未应用本轮新人工过滤结果**的计划数；export 一变必须全量重算。
- 本轮未读取 2026-05-04+ OHLC 来构造 long 数据，未训练、未 promote、未部署、未下单，
  `training_eligible` 未改变。

## 8. 复现命令

```bash
cd /Users/zhangzc/fable-trading

.venv/bin/python scripts/audit_owner_long_gold_center_plan.py \
  --out analysis/output/owner_long_dataset_lineage_audit_20260821/summary.json

.venv/bin/python -m pytest \
  tests/test_ma_rope_review.py \
  tests/test_backtest_owner_short_gold_center_recent.py -q

python3 scripts/md_to_html.py \
  analysis/p1_owner_long_dataset_lineage_audit_20260821.md \
  --out-dir analysis/html
```

机器可读审计：
`analysis/output/owner_long_dataset_lineage_audit_20260821/summary.json`

