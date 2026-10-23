# 15m 六均线平滑度与像素可见度审计（2026-09-02）

性质：P0/P1 非方向性指标与渲染审计；只读冻结 chronological pre-holdout val。  
授权边界：**未训练、未调阈值、未读取 holdout、未改 ACTIVE/frozen、未部署。**  
安全门：所有正例可见窗口为 `2025-12-04 03:15Z` 至 `2026-05-03 20:30Z`，脚本遇到
`window_end_time >= 2026-05-04` 会 fail closed。

## 结论先行

1. **YOLO 图像层早已不是“双均线”，而是六条线**：`close` 上的
   `SMA/EMA × 20/60/120`。双均线只存在于另一条 Pine 入场逻辑（SMA10/SMA60），不要把两层混为一谈。
2. ChartPrime 这段代码的数值主线只有 **一条可选 MA**；`ta.hma(..., 10)` 平滑的是
   “价格到 MA 的归一化距离变化”振荡器，不是主图 MA。它不能直接证明“换成 ChartPrime 的线，YOLO 就会更准”。
3. 冻结 1,200 张正例诊断**不支持“MA 数值不够平滑是主因”**：六线平均像素斜率对正式命中
   几乎没有区分（rank AUC `0.503`, `p=0.909`）；曲率的单变量差异在控制核心根数、方向、
   post 确认位置和包络宽度后消失（每 SD OR `0.865`, 95% CI `0.512–1.460`, `p=0.587`）。
4. 更强的失败轴是**标签/几何与确认阶段**：4 根核心 recall `94.07%`，5 根核心只有
   `79.11%`；post=2 只有 `74.50%`，post=5/7 已达 `90.07%/90.73%`。因此盘口上觉得“不准”，
   首先要怀疑完成态训练分布与新鲜 tip 识别不是同一任务。
5. 存在一个值得验证的**次级渲染问题**：源图为 1px 抗锯齿线，训练输入从 1280 横向缩到 960，
   等效线宽约 0.75px。缩放后六线颜色横向覆盖每增加 1 SD，正式命中 odds 为 `1.521×`
   （95% CI `1.114–2.076`, `p=0.0083`），但这是观察性相关，不能直接宣称 2px 一定有效。
6. 所以下一臂若获 owner 批准，应该只做 **1px → 2px 实线** 的单变量重渲染＋重训；不要同时换
   `hl2`、RMA/HMA、周期、颜色或加 7px glow。旧权重不能直接配新渲染器验收。

## 1. 先把仓内的三套“均线”分清

| 表面 | 实际线 | 用途 | 是否已是六线 |
|---|---|---|---:|
| YOLO L1 模型输入 | close 的 SMA20/60/120 + EMA20/60/120 | 图像检测“密集启动” | **是** |
| Pine V8 基础入场 | hl2 的 SMA10/SMA60；另有 close EMA100 regime | 双线交叉＋趋势过滤 | 否 |
| Pine V12F / 4h causal | close 的 SMA/EMA 20/60/120 | 六线 rope / 交叉拓扑 | **是** |

代码证据：

- `yoyo/layers/l1_detection/data.py` 固定 `MA_PERIODS=(20,60,120)`，逐周期计算 SMA 与 EMA；
- `yoyo/layers/l1_detection/render.py` 逐条画 `ALL_MA_COLS`，当前线宽为 1px、`cv2.LINE_AA`；
- `experiments/active/exp-pine-allin-v7-preholdout-v1/pine/allin_v8_research.pine` 的 V8 才是
  SMA10/SMA60 双线，并已包含一条与 ChartPrime 同族的 SMA40-distance-HMA oscillator；
- `experiments/active/exp-pine-eth-15m-v1/pine/allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine`
  已经计算六条 rope 线及其交叉事件；
- `experiments/active/exp-btc-4h-causal-pine-v1/pine/fable_4h_ma_launch_causal_v1.pine`
  已把六线画在 TradingView 主图上（SMA 2px、EMA 1px）。

因此，“参考他改成六均线”在工程上不是不能做，而是**仓内两个位置已经做过**；当前问题是六线
该怎样被定义、渲染、标注和验收，而不是缺少第 3–6 条线。

## 2. ChartPrime 代码到底算了什么

官方脚本页把它定义为“一条可切换类型的 MA + MA Shift Oscillator”，支持
SMA/EMA/SMMA(RMA)/WMA/VWMA，并用价格相对 MA 的位置着色：
[Moving Average Shift [ChartPrime]](https://www.tradingview.com/script/aApUyBnk-Moving-Average-Shift-ChartPrime/)。

按用户提供的 Pine v6 计算段，可写成：

\[
M_t = F_{type}(hl2_t, 40)
\]

\[
d_t = hl2_t - M_t,\qquad q_t=P_{99}(d_{t-999:t})
\]

\[
r_t=d_t/q_t,\qquad osc_t=HMA_{10}(r_t-r_{t-15})
\]

逐项含义：

| 代码 | 真正作用 | 对“主图线平滑”的影响 |
|---|---|---|
| `type_ma` | 五种滤波器中选一种 | 有影响，但一次仍只有一条 MA |
| `length=40` | 单条 MA 的时间尺度 | 越长通常越平滑，也越滞后 |
| `source=hl2` | 每根 K 的高低中点 | 与 close 是不同语义；影线会直接影响输入 |
| `diff=source-MA` | 价格偏离主线 | 不改变 MA |
| `P99(diff,1000)` | 用过去 1,000 根的偏离尺度归一化 | 不改变 MA；影响 oscillator 稳定性 |
| `change(...,15)` | 当前归一化偏离减 15 根前 | 是动量/位移变化，不是第二条 MA |
| `HMA(...,10)` | 平滑该变化序列 | 只平滑 oscillator，不平滑主图 MA |

TradingView 官方说明 MA 天生是滞后/反应型工具，平滑噪声与反应速度之间有交换关系；SMA 对窗口
内数据等权，EMA/WMA 更重视近期数据，因此更快但不等于更平滑：
[Moving Averages](https://www.tradingview.com/support/solutions/43000502589-moving-averages/)、
[Simple Moving Average](https://www.tradingview.com/support/solutions/43000696841-simple-moving-average/)、
[Exponential Moving Average](https://www.tradingview.com/support/solutions/43000592270-exponential-moving-average/)。
HMA 官方公式是 `WMA(2*WMA(n/2)-WMA(n), sqrt(n))`，目标是兼顾速度和平滑；这个差分结构也意味着
它不是“永远更稳”的无成本替代：
[Hull Moving Average](https://www.tradingview.com/support/solutions/43000589149-hull-moving-average/)。

### 2.1 各类型若移植到六线，实际代价

| 类型 | 视觉/数值特性 | 对本项目的判断 |
|---|---|---|
| SMA | 等权、慢、拐点较钝 | 当前六线的一半；保留基线 |
| EMA | 近期权重大、反应快 | 当前六线的一半；保留基线 |
| SMMA/RMA | 比同长度 EMA 更慢、更平滑 | 会延后启动/排序；不能用“更平滑”直接判优 |
| WMA | 线性增大近期权重 | 通常更敏感，不是天然降噪方案 |
| VWMA | 按成交量加权 | 引入交易所/合约的 volume 语义，跨 venue 稳定性风险最高 |
| HMA | 低滞后目标、差分 WMA | 可出现更快弯折/过冲；且原脚本只把它用于 oscillator |

TradingView 对内置 smoothing 类型与 length 的官方说明见
[Smoothing settings](https://www.tradingview.com/support/solutions/43000742042-i-see-a-smoothing-section-in-an-indicator-s-settings-what-does-it-do/)，
对 VWMA 的定义见
[Volume Weighted Moving Average](https://www.tradingview.com/support/solutions/43000592293-volume-weighted-moving-average-vwma/)。

### 2.2 原公式自身有一个与“线平滑”无关的风险

`perc_r` 是 **signed `diff`** 的 99 分位数，不是 `abs(diff)` 的分位数。用户所贴片段没有给
`perc_r≈0` 或负值做保护；在长期单边、99 分位仍接近 0 或为负时，`diff/perc_r` 可能放大或翻转
符号。这会污染 oscillator，却不会让主图 MA 更平滑。

本仓 V8 已至少做 `!=0` 保护；V12F 更严格要求 `percentile99 > 0`。若将来研究这条 oscillator，
应把“分母安全/是否用绝对偏离”作为单独变量，不能与六线渲染一起改。

## 3. 本轮冻结诊断

### 3.1 数据与正式口径

| 项目 | 值 |
|---|---:|
| val 总图 | 4,800 |
| 正例 / 正类率 | 1,200 / 25.0% |
| hard + easy 空标签负例 | 2,400 + 1,200 |
| 独立正事件 | 155 |
| 行情源文件 | 142 |
| 可见时间范围 | 2025-12-04 03:15Z ～ 2026-05-03 20:30Z |
| 正式命中 | 类别正确且 GT IoU≥0.5 |
| 固定推理 | imgsz=960、conf=0.25、NMS IoU=0.70 |

账本复核完全对齐上一份冻结评估：

| 指标 | 原冻结报告 | 本轮重算 | 差异 |
|---|---:|---:|---:|
| 正例正式命中 | 1,035 / 1,200 | 1,035 / 1,200 | 0 |
| 独立事件任一变体命中 | 146 / 155 | 146 / 155 | 0 |
| 空标签负图出框 | 37 / 3,600 | 37 / 3,600 | 0 |

原模型完整验证仍是 P `0.8528`、R `0.8532`、mAP50 `0.9116`、mAP50-95 `0.7775`；本轮
没有重新训练或选择 checkpoint。

### 3.2 “数值不平滑”与命中关系

单变量 Mann–Whitney 检验的零假设是“正式命中与漏检的指标分布相同”；rank AUC `0.5` 表示无
区分，>0.5 表示指标越大越容易命中，<0.5 表示越大越难命中。

| 指标 | 命中中位数 | 漏检中位数 | rank AUC | 双侧 p | 解读 |
|---|---:|---:|---:|---:|---|
| 六线平均绝对斜率（px/bar） | 4.127 | 4.093 | **0.503** | **0.909** | 没有证据 |
| 六线平均绝对曲率（px/bar²） | 1.479 | 1.686 | 0.429 | 0.00338 | 单变量看似更抖更难 |
| pairwise order flips | 3 | 3 | 0.463 | 0.114 | 没有稳定分离 |
| MA slope dispersion（ATR） | 0.1366 | 0.1405 | 0.441 | 0.0142 | 弱，且混合几何因素 |
| 六线核心平均包络（bp） | 46.46 | 63.73 | 0.405 | 9.4e-5 | 宽包络更难 |
| 六线平均像素展开 | 68.67 | 79.06 | 0.405 | 8.6e-5 | 宽包络更难 |

但 1,200 张图不是独立事件：一个事件贡献 post2–9 多个变体。用二项 logistic、按 155 个
`event_id` 聚类稳健标准误，并在建模前固定控制 `core_bars`、方向、post 类别及像素包络后：

| 项 | 每 1 SD OR | 95% CI | p |
|---|---:|---:|---:|
| 平均绝对曲率 | **0.865** | 0.512–1.460 | **0.587** |
| 像素包络宽度 | 0.738 | 0.448–1.216 | 0.233 |
| 5 根核心 vs 4 根 | **0.209** | 0.093–0.472 | 0.000166 |

曲率的 CI 同时容许明显负向和正向影响，不能说“换更平滑的 MA 已被数据支持”。5 根核心的
影响远大且稳定，说明标签几何/核心定义才是优先排查轴。

### 3.3 确认阶段比平滑度更能解释漏检

| 分层 | n | recall |
|---|---:|---:|
| core=4 | 573 | **94.07%** |
| core=5 | 627 | **79.11%** |
| post=2 | 149 | **74.50%** |
| post=3 | 153 | 82.35% |
| post=4 | 147 | 89.12% |
| post=5 | 151 | 90.07% |
| post=7 | 151 | 90.73% |
| post=9 | 152 | 86.18% |

当前正图带核心后 2–9 根 K 线，属于 completed-history / delayed-confirmation 检测器。任一后续变体
命中率 94.19% 不能冒充 tip 盘口能力；最早 post=2 只有 74.50%。如果用户说的是“最新一根总认不出”，
这与训练合同不匹配直接一致，不需要先假设均线抖动。

### 3.4 1px 线在 960 输入中的可见度是次级线索

源 PNG 为 1280×742，六线用 1px `LINE_AA`。训练 `imgsz=960` 时横向比例为 0.75；本轮用同样
的横向缩放，测每个像素到六个冻结 BGR 线色的 L∞ 色差，以及有线色像素的横向列覆盖。

| 指标 | 命中中位数 | 漏检中位数 | rank AUC | p |
|---|---:|---:|---:|---:|
| 原生 1280 线色密度 | 0.004375 | 0.004441 | 0.495 | 0.827 |
| 960 线色密度 | 0.000807 | 0.000719 | 0.583 | 0.000629 |
| 960 横向覆盖 | 0.3552 | 0.3135 | 0.597 | 5.70e-5 |
| 缩放后色质量保留比 | 0.1839 | 0.1624 | 0.586 | 0.000360 |

控制 `core_bars`、方向、post 类别与六线末端 bp 包络后，960 横向覆盖每 1 SD 的 OR 为
**1.521**（95% CI `1.114–2.076`, `p=0.00829`）；末端包络每 1 SD OR 为 `0.588`
（95% CI `0.440–0.787`, `p=0.000346`）。

颜色容差敏感性：

| L∞ 容差 | rank AUC | p | 判断 |
|---:|---:|---:|---|
| 10 | 0.516 | 0.509 | 太严，绝大部分 AA 像素被排除 |
| 16 | 0.585 | 0.000443 | 同方向 |
| 22 | 0.597 | 5.70e-5 | 主诊断值 |
| 28 | 0.618 | 1.02e-6 | 同方向 |
| 36 | 0.573 | 0.00244 | 同方向 |
| 48 | 0.544 | 0.0651 | 接近全列饱和 |

中段阈值方向一致，极严/近饱和端不显著，说明不是单个阈值的偶然翻转；但“可见覆盖高”也与
形态几何纠缠，仍然只是**提出 2px 因果消融的依据**，不是证明 2px 会提升模型。

## 4. 历史证据：渲染一致性确实曾比模型容量更关键

早期同一数值密集规则在低波动图上被动态 Y 轴过度放大，导致视觉上不再像“六线挤在一起”；
加入 `MIN_REL_SPAN=0.06` 后，smoke mAP50 从约 `0.22` 跳到 `0.68`。这证明模型会被像素表达
伤害，但当时病因是**纵轴尺度合同**，不是 MA 类型不够平滑。随后更大模型并未带来同量级改善。

当前 `MIN_REL_SPAN` 已冻结，所以本轮建议只验证尚未隔离的线宽问题。

## 5. 如何“参考 ChartPrime 做六均线”才不污染结论

### 推荐的单变量顺序

| 臂 | 唯一改动 | 其余冻结 | 用途 | 当前建议 |
|---|---|---|---|---|
| A | 无 | 现有 close-SMA/EMA20/60/120、1px | baseline | 已有 |
| B | 1px → **2px 实线** | 数值、颜色、窗口、标签、split、训练配方 | 检验亚像素可见度 | **第一优先，需 owner 批准训练** |
| C | close → hl2 | 仍为六线、宽度固定为胜出值 | 检验 source | B 之后 |
| D | MA 家族单独替换 | source/周期/线宽全部冻结 | 检验 RMA/HMA 等 | 最后；需重建相容语义 |

六条 7px glow 不适合作为 B：密集区会融合成色带，消掉六线的相对顺序与交叉拓扑。若目标只是
TradingView 人眼观感，可像仓内 4h Pine 一样使用 SMA 2px、EMA 1px；若目标是 YOLO，先只测
六条均为 2px 实线，最容易归因。

### Pine v6 的六线数值核心

下列核心与当前 YOLO 合同一致，不复制 ChartPrime 的 oscillator：

```pine
float sma20  = ta.sma(close, 20)
float ema20  = ta.ema(close, 20)
float sma60  = ta.sma(close, 60)
float ema60  = ta.ema(close, 60)
float sma120 = ta.sma(close, 120)
float ema120 = ta.ema(close, 120)

plot(sma20,  "SMA20",  color=#0EA5E9, linewidth=2)
plot(ema20,  "EMA20",  color=color.new(#38BDF8, 25), linewidth=1)
plot(sma60,  "SMA60",  color=#F59E0B, linewidth=2)
plot(ema60,  "EMA60",  color=color.new(#FBBF24, 25), linewidth=1)
plot(sma120, "SMA120", color=#8B5CF6, linewidth=2)
plot(ema120, "EMA120", color=color.new(#A78BFA, 25), linewidth=1)
```

TradingView 官方说明 `plot()` 的 `linewidth` 对线图按像素控制粗细：
[Pine plots](https://www.tradingview.com/pine-script-docs/visuals/plots/)。若直接修改并再分发 ChartPrime
原源码，还需保留它的 MPL-2.0 许可与来源；上述六线核心来自本仓既有合同。

## 6. 下一臂的验收口径（若 owner 批准）

2px 臂必须从源 K 重渲染全套 train/val 并重训。禁止拿旧 1px 权重直接推 2px 图，因为那只测
预处理分布失配。

主指标：

1. post=2 独立事件 recall；
2. core=5 recall，同时守住 core=4；
3. 3,600 张空标签负图出框率 / 每千图假框；
4. 预测框方向与 IoU≥0.5 的正式 recall；
5. 语义门前后事件保留与背景压制。

mAP50/mAP50-95 继续报告，但不单独裁决；更不能用 val mAP 代替 tip-smoke 或生产资格。

建议的先验成功门（仅供 owner 决策，尚未写入训练合同）：post=2 与 core=5 有可重复提升，且
负图出框不劣化；具体最小提升幅度应在启动训练前由 owner 冻结，不能看完结果再定。

## 7. 零假设、统计限制与非方向性说明

- 本轮没有收益标签、方向性策略、TP/SL 或成本，因此 **val AUC、top-decile 收益、胜率、
  单特征收益基线、匹配随机入场对照均不适用**；不得编造。
- 同等严格零假设为：在冻结正式 hit/miss 下，均线粗糙度或线色覆盖与命中无关联。
  单变量使用 Mann–Whitney rank AUC；多变量使用 event-cluster robust logistic，报告 OR 与 95% CI。
- 单变量表为探索性多指标扫描，未做多重比较校正；推断优先看预先按机制选择的聚类稳健模型，
  仍不作因果解释。
- 色差阈值不是物理“可见/不可见”的真值，所以报告完整容差敏感性，不从中挑最佳阈值。
- 观察性分析不能回答 2px 是否提升；只有单变量重渲染＋重训能回答。

## 8. 风险与诚实声明

1. **最容易被误读的点**：当前同分布 event-any recall 94.19% 很高，是因为一个事件可看 post2–9
   多个完成态变体；它不表示盘口 tip 可用。
2. 更平滑通常意味着更滞后。若把 RMA/HMA 直接替换六线，owner 原来确认的“启动前一根核心右端”
   可能整体移动，旧标签不再是同一语义。
3. `hl2` 与 close 不是纯降噪开关；长影线会改变 `hl2`，必须单独验证。
4. VWMA 会把 venue-specific volume 带入像素，当前不推荐作为第一臂。
5. 未动 holdout；所有结论均为 pre-holdout 发现级。未训练、未 promote、未改生产 detector。

## 9. 复现命令与产物

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=. python3 scripts/audit_six_ma_smoothness_visibility.py
python3 scripts/md_to_html.py \
  analysis/p1_15m_six_ma_smoothness_visibility_audit_20260902.md \
  --out-dir analysis/html
```

产物：

| 文件 | SHA-256（生成时） |
|---|---|
| `scripts/audit_six_ma_smoothness_visibility.py` | `1e5592c638e8ea6daa51dac5704aadb95428dc1cf20b5c918d962b1f4b173ae5` |
| `analysis/output/p1_six_ma_smoothness_visibility_audit_20260902.json` | `cb26f386d182d6af360fcacf404e58778ac0485793e891c6d971b39956e0c52e` |
| `docs/learnings/ma-numeric-smoothness-and-pixel-visibility-are-separate-hypotheses.md` | `e73a5e7392c0fd6410e8790390a3b1105b466e7d8bbdabb4055bed878f022907` |

## 10. 下一步选项

| 选项 | 动作 | 是否需要 owner 决策 |
|---|---|---:|
| A（推荐） | 冻结 2px-only 实验合同与成功门，再决定是否允许重渲染/重训 | **是** |
| B | 只把现有六线 Pine 用于 TradingView 人工观察，不动 YOLO | 否（不接实盘/alert） |
| C | 直接换 RMA/HMA/hl2 或六线 glow | **不建议；若做，需逐变量批准** |
| D | 当前不改，先继续 P0/P1 样本语义与 tip 金标 | 否 |

本轮推荐 **D 保持主线，A 作为待 owner 批准的最小消融**；不建议把“平滑”当作一次打包改线、
改源、改周期和改标签的理由。
