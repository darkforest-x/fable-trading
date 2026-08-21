# ETH 15m Pine：335 候选自动先达标签与判断门优化审计

生成日期：2026-08-21

实验：`exp-pine-eth-15m-v1`

范围：2023-01-01 至 2024-12-31 开发区间；15 分钟固定
状态：**标签审计完成；ATR 扩张门淘汰；未训练 LR/LightGBM；不可 forward / production**

## 技术结论

1. **335 个候选已经全部自动标完，不需要人工审核。** 184 个正例、151 个负例，正例率
   **54.93%**；0 个 ambiguous、0 个 censored。所谓“标签审计”只是让程序核对未来价格路径，
   不是让人逐张判断。
2. 正例定义固定为：信号 `t` 确认、`t+1` 开盘入场后，方向调整的 **+1.5%** 先于
   冻结 V9 初始止损或下一反向 guarded signal 的次根开盘到达。止损仍为
   `min(4×signal ATR14, signal close×3%)`，0.01 tick；没有改 TP/SL/成本。
3. 54.93% 不能解释成“交易胜率”。`+1.5%` 在 V9 里只会让下一根 bar 起启用 `+0.1%`
   break-even stop；扣 20bp 往返成本后，BE 退出仍亏 10bp。实际开发账本的净胜率只有
   **14.46%（2023）/ 19.28%（2024）**，两者衡量的是不同事件。
4. 28 个现有 L2 特征对该标签的单特征信号偏弱：最强 `atr_pct` 全样本 AUC **0.5666**，
   28 特征 family-wise 标签置换 `p=0.3723`。这不证明多变量 LR 一定无效，但不支持现在就把
   旧 LR/LightGBM 接进来。
5. Pine 候选的先达率比严格匹配随机入场高 **4.78 个百分点**（54.93% vs 50.15%），
   但 103 个周区块 sign-flip `p=0.0970`，未达到项目 `p<0.01` 门槛。
6. 已实跑一个不训练模型的单变量优化：`atr_pct_ratio96 >= 1.0`（当前 ATR 高于过去 96 根均值）。
   它把 2023 资金收益从 **+70.50% 降到 -2.24%**，把 2024 从 **+109.26% 降到 +60.59%**，
   且 2024 回撤由 12.22% 升到 15.66%。**淘汰的是它作为当前组合的 entry gate，不是否定
   ATR expansion 特征在其他目标中的全部价值；本轮不写入 Pine。**

因此，这一轮真正的优化结果是：标签管线已从“待人工”变成 335/335 自动、因果、可复算；同时反证了
“用波动扩张直接过滤入场”这一条看似合理的改法。下一步应先改善目标与特征的可学性，再做动态 LR，
不能为提高表面胜率直接改止损或堆 gate。

## 335 个候选全部有确定路径

候选总体是 2023–2024 所有通过日历与 ATR guard 的 V9 raw signals，不是基线账本里已经执行的
166 笔。拒绝某一候选会改变持仓、反转和 cooldown，因此判断层必须覆盖完整 335 行。

| 切分 | 候选 | 正例 | 负例 | 正例率 | Ambiguous | Censored |
|---|---:|---:|---:|---:|---:|---:|
| 全部 | 335 | 184 | 151 | 54.93% | 0 | 0 |
| Long | 170 | 92 | 78 | 54.12% | 0 | 0 |
| Short | 165 | 92 | 73 | 55.76% | 0 | 0 |
| 2023 | 167 | 83 | 84 | 49.70% | 0 | 0 |
| 2024 | 168 | 101 | 67 | 60.12% | 0 | 0 |

事件拆分为 184 次 target first、148 次 initial stop first、3 次 opposite signal exit first。
事件中位发生于入场后 22 根 15m bar（约 5.5 小时），P90 为 104.6 根（约 26.2 小时）。

### 为什么没有人工审核

解析器按如下顺序自动工作：

1. 先处理 bar open gap；
2. 15m 只触碰一边时直接定序；
3. 同一 15m 同时触碰目标与止损时，下钻按时间排序的 3m 子柱；
4. 同一 3m 仍双触碰时标记 ambiguous 并排除，绝不猜固定 OHLC 路径。

本轮 335 个真实候选没有发生任何 15m 双触碰，所以全部由 15m 唯一事件解析。3m 文件在开发范围内有
249,376 行、49,875 个完整 15m parent；聚合后 open/high/low/close 与主 15m 源的误差和错配均为 0。
匹配随机对照里有 3 个发生在 3m 覆盖起点之前的双触碰；程序自动拒绝并从同一严格分层补选，仍然不需要人。

## “先达率”只比随机高 4.78pp，证据尚不够强

每个 Pine 候选匹配 3 个不复用随机入场，共 1,005 行。匹配条件固定为同一 ETH、同 UTC 月、同香港
6 小时时段、用**前一个月** ATR 分布形成的同五分位、相同方向与相同前瞻 horizon；目标、止损完全一致。

| 指标 | Pine 候选 | 匹配随机 | 差值 |
|---|---:|---:|---:|
| +1.5% 先达率 | 54.93% | 50.15% | +4.78pp |
| 等权周区块差值 | — | — | +4.70pp |
| 周 sign-flip 单侧 p | — | — | 0.0970 |

这属于“方向正确但未过证据门”：点估计支持双均线启动候选比随机稍好，但 `p=0.0970` 不能排除制度与
样本波动。first-touch 是非收益标签，因此这里没有伪造成本后净收益；同障碍/同 horizon 是适用的严格零假设。

## 现有 28 特征还不足以可靠挑出正例

下面是描述性单特征结果。特征全部在信号 bar `t` 及以前计算；未来只进入标签。`oriented AUC` 把
低值更好的特征翻转到 0.5 以上，便于比较强度，不能当成已经验证的模型表现。

| 特征 | 全部 AUC | Oriented AUC | 2023 AUC | 2024 AUC | 稳定方向 |
|---|---:|---:|---:|---:|---|
| `atr_pct` | 0.5666 | 0.5666 | 0.5211 | 0.5747 | 高值较好 |
| `atr_pct_ratio96` | 0.5556 | 0.5556 | 0.5377 | 0.5704 | 高值较好 |
| `drawdown24` | 0.5424 | 0.5424 | 0.5211 | 0.5602 | 高值较好 |
| `order_score` | 0.4601 | 0.5399 | 0.4753 | 0.4371 | 低值较好 |
| `volume_ratio` | 0.4680 | 0.5320 | 0.4604 | 0.4568 | 低值较好 |

28 特征中最大 `|AUC-0.5|=0.0666`；对标签做 2,000 次确定性 shuffle、每次重新取 28 特征最大值后，
family-wise `p=0.3723`。这说明目前看到的最好 AUC 在多重比较下并不稀有。

项目规定的 val AUC、top-decile 毛/净收益和模型置换检验在这里**不适用**：本轮没有训练模型、没有分数、
没有阈值，也没有 top-decile。为避免留空冒充，我们用逐年单特征 AUC、family-wise 标签 shuffle 和
匹配随机先达率作为同等严格的零假设审计。

## 单变量 ATR 扩张门动态回放失败

为了验证“高波动启动更容易到 +1.5%，是否就能提高收益”，仅增加一个自然规则：
`atr_pct_ratio96 >= 1.0`。阈值 1.0 表示当前 ATR% 不低于过去 96 根均值，没有跑阈值网格；信号、止损、
BE、反向、cooldown、1% 风险仓位和 20bp 成本全部不变。

| 时段 / Arm | Raw/接受 | 交易 | 净 bp/笔 | 资金收益 | PF | 净胜率 | DD | 对照 bp | 超额 bp | 周 p | 去 top1 bp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2023 Allow-all | 167/167 | 83 | +52.01 | +70.50% | 1.921 | 14.46% | 26.48% | +13.19 | +38.82 | 0.0496 | +22.39 |
| 2023 ATR expansion | 167/78 | 47 | +21.05 | **-2.24%** | 0.910 | 12.77% | 15.41% | +4.50 | +16.54 | 0.5184 | **-26.49** |
| 2024 Allow-all | 168/168 | 83 | +141.35 | +109.26% | 2.748 | 19.28% | 12.22% | +32.55 | +108.81 | 0.0097 | +110.68 |
| 2024 ATR expansion | 168/77 | 49 | +150.87 | **+60.59%** | 2.867 | 8.16% | **15.66%** | +50.88 | +99.99 | 0.1390 | +28.39 |

门控相对 allow-all：2023 资金收益少 **72.74 个百分点**；2024 少 **48.67 个百分点**，且回撤增加
3.44 个百分点。2023 的“平均 project net bp/笔为正而资金收益为负”不是计算矛盾：前者是未按资金权重
平均的单位收益，后者包含随止损距离变化的实际仓位与按名义本金计费，盈利/亏损交易权重不同。

该门还把尾部依赖放大：2023 最大一笔占净和 223.2%，去掉后 -26.49bp/笔；2024 最大一笔占 81.57%，
去掉后只剩 +28.39bp/笔。标签先达率改善没有转化成稳定经济改善。

### 64 个匹配对照分配种子

单个匹配随机分配会波动，因此对四个 arm×时期各跑 64 个确定性种子。超额 bp/笔的中位与 P05–P95：

| 时段 / Arm | 中位超额 bp | P05 | P95 | 超额为正种子占比 |
|---|---:|---:|---:|---:|
| 2023 Allow-all | +49.50 | +30.75 | +67.89 | 100.00% |
| 2023 ATR expansion | +25.88 | -4.67 | +55.43 | 89.06% |
| 2024 Allow-all | +117.56 | +81.37 | +139.01 | 100.00% |
| 2024 ATR expansion | +96.70 | +10.51 | +154.10 | 96.88% |

ATR 门的匹配超额点估计大多仍为正，但比 allow-all 更低，锚定周检验也不显著；更重要的是资金收益和
尾部稳健性直接恶化。因此淘汰结论不依赖挑选某个“坏”对照种子。

这里的 64 个种子只是 **matched-control assignment sensitivity**，不是 64 次独立策略回测，更不是
64 个 OOS 时段；它只检验结论是否依赖某一次随机对照分配。

## 为什么脚本实际胜率仍然很低

当前退出结构决定了低胜率并不等于均线信号完全随机：

- 很多候选确实先涨/跌到 +1.5%，所以 start label 为 1；
- 但目标并不止盈，只在该 15m bar 结束后把下一 bar 的 stop 提到 +0.1%；
- 20bp 往返成本把 +0.1% 毛锁盈变成 -0.1% 净收益；
- 真正的大额盈利主要来自少数一直持有到反向信号的长趋势交易；
- 任意 entry gate 都可能删掉这些长尾赢家或改变随后反转链，因此分类胜率与最终收益必须分开验收。

这也是本轮不能“为了提高胜率”直接把 +1.5% 改成 take-profit、把 BE 提到更高或缩窄止损的原因：这些都
会改变 owner-controlled 障碍/执行参数，且需要独立单变量实验和明确批准。

## 数据边界与稳健性

| 审计项 | 结果 |
|---|---|
| 15m 加载 | 104,962 行；2022-01-03 15:30 → 2024-12-31 23:45 UTC |
| 15m cadence | 重复 0；非递增 0；非 15m gap 0 |
| 候选范围 | 2023–2024，335 行，28 特征无缺失 |
| 3m 加载 | 249,376 行；2023-07-31 11:12 → 2024-12-31 23:57 UTC |
| 完整 3m parent | 49,875；与 15m OHLC 错配 0 |
| Consumed-final 行进入分析帧 | 0 |
| 边界 chunk 的物理 I/O | 15m 解析后排除 38 行 final；3m 解析后排除 624 行 final；均未进入特征/标签 |
| Holdout 行读取 | 0；未评估、未消耗 |
| 模型/阈值 | 未训练、未评分、未选择 |
| 资格 | training / forward / production 均为 false |

Luna Max 做了两轮只读独立复核。第一轮正确指出需要补 15m cadence 断言，但把“旧 3m 审计函数过滤到
2025”误读为“源文件 2025 才开始”；本线程通过 bounded raw loader 核实真实起点为 2023-07-31，并以
49,875 个 parent 的逐 OHLC 对账纠正。第二轮独立重算确认：335/335 标签逐字段一致、1,005 条控制匹配
契约全通过、四组 64-seed 摘要误差小于 `1e-12`，并指出两个已修正/披露事项：标签目标不能借用可变的
BE 参数，以及 loader 会物理解析边界 chunk 后再排除 final 行。子线程意见只作为复核，不替代本地证据。

## 风险与诚实声明

- 335 个标签全部自动解析，不代表标签就是最优经济目标；它只衡量启动后先到 +1.5%。
- 2024 已在本轮特征审计中被查看，因此 ATR 门的 2024 结果是探索性反证，不是新鲜 confirmation。
- 标签路径高度重叠，普通逐行 shuffle 只用于 feature encoding 的描述性 family-wise null；不能代替
  动态账本、时间区块检验或真正前向样本。
- 3m 数据不覆盖 2023-01 至 2023-07；真实候选未在该段发生 15m 双触碰，因此不影响 335 标签；
  对照样本的 3 个歧义已 fail-closed 替换。
- 本轮没有让任何 final-preholdout 行进入分析，但 CSV chunk loader 为识别 2025-01-01 边界，物理解析后
  排除了 38 条 15m 与 624 条 3m final 行；这些行没有进入特征、标签、收益或选参。holdout 完全未触碰。
  历史 V9 final 结果仍是已消耗证据，不得用它继续选标签、特征或阈值。
- 未做 TradingView 逐笔导出 parity；本地数据是 OKX `ETH-USDT-SWAP`，不能把 `ETHUSDT.P` 显示名
  当成交易所一致性证明。

## 下一步

1. **保留自动标签管线，淘汰 ATR 扩张 entry gate。** 不修改当前 V9 Pine。
2. **在 P0/P1 内先做标签经济性审计。** 把 start-positive 细分成最终 BE-stop、reverse winner、initial stop，
   检查判断层究竟应预测“启动”还是“能覆盖成本的结算结果”。
3. **新增一个真正针对假启动的因果特征，单变量验证。** 优先考虑交叉前趋势效率/震荡度，而不是再用
   ATR level；选择只能在更早时间块完成，随后必须动态回放。
4. **LR/LightGBM 暂不训练。** 当前 family-wise `p=0.3723` 且项目 P0/P1 未放行；通过标签目标与
   Gold Dataset 门后，再以 2023 calibration → 2024 evaluation 的时间切分训练，并对 335 raw surface
   全量 next-open 评分。
5. 任何 TP/SL、BE 触发/偏移、成本或 ATR floor 改动都需要 owner 明确逐项批准；本轮没有偷改。

进一步需要回答的问题是：低胜率主要来自 entry 假启动，还是来自“+1.5% 后不止盈、只锁 +0.1%”的退出
结构？当前证据显示两者都有，但后者足以让 first-touch 正例变成净亏交易。下一轮应先做结算目标拆分，
再决定 LR 预测什么。

## 复现命令与产物

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_pine_eth_15m_start_labels.py \
  tests/test_pine_eth_15m_start_label_artifacts.py \
  tests/test_prepare_pine_eth_15m_gate_surface.py \
  tests/test_replay_pine_eth_15m_judgment_gate.py

PYTHONPATH=. .venv/bin/python scripts/audit_pine_eth_15m_start_labels.py
PYTHONPATH=. .venv/bin/python scripts/evaluate_pine_eth_15m_atr_expansion_gate.py

python3 scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_start_label_audit_20260821.md \
  --out-dir analysis/html
```

主要逐笔/逐候选产物：

- `experiments/active/exp-pine-eth-15m-v1/results/judgment_start_label_rows.csv`（335 行）
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_start_label_matched_controls.csv`（1,005 行）
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_start_label_feature_audit.csv`
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_atr_expansion_gate_trades.csv`
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_atr_expansion_gate_controls.csv`
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_atr_expansion_gate_pairs.csv`
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_atr_expansion_gate_control_sensitivity.csv`（256 行）
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_start_label_audit.json`
- `experiments/active/exp-pine-eth-15m-v1/results/judgment_atr_expansion_gate.json`

本报告未放图：核心证据只有两个年度切片和精确审计表，柱图不会增加信息，表格更适合逐项核对。
