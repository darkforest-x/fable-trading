# BTC 4h 双均线密集启动：因果 Pine V1（2026-08-25）

## 结论先行

已经写成一版可直接加载的 Pine v6 指标，并在 TradingView 官方编辑器完成真实编译：**0 个编译错误，编辑器源码与仓库文件 SHA-256 完全一致**。脚本已保存为私有脚本 `Fable 4H MA Launch Causal V1 · Research Only`，并添加到 `OKX:ZECUSDT.P · 4h · Candles` 图表；没有发布、创建 alert、发送 webhook 或下单。

这版与刚才找币的完成形态检索**属于同一类形态、共享冻结结构参数，但不是同一个决策配置**。找币版必须看启动后 12 根 4h K 线，约 48 小时后才能确认；本 Pine 只允许看当前已收盘 bar 以及最多前 2 根启动 bar，信号画在真实确认时刻，不回填到过去。因此它能用于实时观察，但完成形态检索的相似度、p 值和候选质量结论不能移植给它。

本轮证明的是“源码满足已冻结的因果合同，并能通过官方 Pine 编译器”，**没有证明信号赚钱、命中率高或可上生产**。没有新增读取或评分 repository holdout，没有训练、回测、forward、promote 或交易动作。不过，本配置继承了完成形态父配置中由边界后 Owner 参考形态冻结的门槛，所以这些产物在注册表中继承 `holdout_consumed` 血缘；“本轮零新增读取”不等于“独立 pre-holdout 证据”。

## Pine 做了什么

- 只在普通蜡烛图的 **4 小时**周期生效；其他周期 HUD 显示 `SWITCH TO 4H` 且不发信号。
- 使用当前图表币种的 SMA/EMA 20、60、120 六条均线，不调用 `request.*`，不跨币取数。
- 候选启动点只允许是当前 bar、tip-1 或 tip-2；分别观察 1、2、3 根释放 K 线。
- 每个判断都等待当前 K 线收盘，信号只画在当前确认 bar，`offset=0`。
- 绿色 `L` 是多头候选，红色 `S` 是空头候选；空头是多头条件的严格符号镜像。
- 同方向信号间隔至少 4 个 bar，避免同一段启动被连续提示。
- `alertcondition()` 只声明可选条件。Owner 仍需在 TradingView 手动创建 alert，脚本自身不会发送 alert 或订单。

## 与完成形态检索的合同关系

| 项目 | 刚才找币的完成形态 V1 | 本轮因果 Pine V1 |
|---|---|---|
| 类别 | 六均线密集后的多/空启动 | 相同类别 |
| 启动前上下文 | 30 根 4h | 30 根 4h |
| 结构门 | 六线密集度、开盘离线束距离、启动前区间 | 原值冻结沿用 |
| 启动后可见 | 12 根 4h | 只看已发生的 1–3 根 |
| 决策延迟 | 约 48 小时 | 当前 4h 收盘确认 |
| RMSE / DTW | 完成形态距离的一部分 | 删除；实时不可观测 |
| 统计证据 | 完成形态 phase-scramble null | 不继承，尚无经济/命中证据 |
| 信号位置 | 离线检索的历史启动点 | 实际确认 bar，绝不回填 |
| 用途 | 找已经走完的类似案例 | 实时研究提示 |

“新配置”具体指**决策时间和可见特征改变**，不是说重新发明一套毫无关系的形态。它是完成形态 V1 的 causal-prefix sibling：六均线和结构门来自同一个冻结父配置；未来 12 根、完成形态 RMSE/DTW 和相应 p 值则依法删除。

## 冻结门槛

结构门沿用参考形态生成的固定值：

| 门槛 | 冻结值 |
|---|---:|
| 启动前六线 spread 上限 | 1.3330854020% |
| anchor open 到线束中心距离上限 | 2.0000000000% |
| 启动前 30 根价格区间上限 | 8.0000000000% |
| 完成形态前 3 根收盘移动下限 | 1.0835936597% |
| 完成形态 12 根收盘移动下限 | 4.3590471546% |
| 完成形态 12 根有利移动下限 | 5.0465594763 ATR |

实时只能看到前 1–3 根，因此在写源码前冻结了确定性的按根折算下限：收盘移动每根取 `max(1.0835936597 / 3, 4.3590471546 / 12)`，有利移动每根取 `5.0465594763 / 12 ATR`。

| 已观察释放 bar | 收盘移动下限 | 有利移动下限 |
|---:|---:|---:|
| 1 | 0.363254% | 0.420547 ATR |
| 2 | 0.726508% | 0.841093 ATR |
| 3 | 1.089762% | 1.261640 ATR |

这是预注册的工程假设，不是从因果样本训练出来的最优值。后续若修改折算方式、lag、结构门、权重或去重长度，必须产生新版本，不能覆盖本 V1。

## 诊断分数

合格候选会显示 0–100 的解释分数，用于在同一时刻多个 lag 同时合格时选择较强者：

| 因果分量 | 权重 |
|---|---:|
| 已确认收盘移动进度 | 40% |
| 收盘离开六均线中心的方向性进度 | 20% |
| spread / anchor 距离 / 前区间压缩质量 | 15% |
| 实体相对启动前 ATR 的方向性进度 | 10% |
| 释放量相对前 30 根均量的对数比 | 15% |

分数**不参与信号准入**，不是概率、胜率、预期收益或生产 confidence。它只解释“同一根确认 bar 上，哪个已经合格的 anchor lag 更像有力启动”。

## 验证结果

### 静态合同

执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_btc_4h_causal_pine.py \
  tests/contracts/test_registries.py \
  tests/boundaries/test_experiment_isolation.py
```

结果：`26 passed, 2 skipped`。覆盖 Pine v6 `indicator()`、`barstate.isconfirmed`、4h fail-closed、lag 仅 0/1/2、禁止 `request.*` / lookahead / strategy / 订单 / 回画、冻结常数与预注册一致、多空镜像及去重合同。

### TradingView 官方编译

| 检查项 | 实际观察 | 裁决 |
|---|---|---|
| 本地源码 SHA-256 | `55512e102b148550d052fab431c0bdc191f99967d9f43805790a79c35932c8be` | PASS |
| 文件规模 | 18,483 bytes / 365 lines | 记录 |
| 编辑器文本 | 17,542 characters / 366 split lines（末尾空行计入） | 记录 |
| 编辑器源码与本地源码 | exact match | PASS |
| Pine 版本 | v6 | PASS |
| 页面 | `OKX:ZECUSDT.P · 4h · Candles` | PASS |
| 官方编译错误 | 0 | PASS |
| 图表 legend | `Fable 4H Launch Causal V1` | PASS |
| 保存状态 | 私有脚本已保存 | PASS |
| 发布 / alert / order | 0 / 0 / 0 | PASS |

官方编译回执位于 `experiments/active/exp-btc-4h-causal-pine-v1/results/tradingview_compile_receipt.json`。编译时原有 TradingView 布局显示了当前、晚于 holdout 边界的市场图，但没有查看候选标记、候选数量、诊断分数、收益、胜率或任何绩效面，也没有据此选参；这只是一条编译 smoke，不是新的 holdout 评价。

## 数据统计与零假设对照

本轮是源码实现与外部编译审计，不是方向性收益实验：

| 项目 | 本轮数值 |
|---|---:|
| repository 市场 bar 读取 | 0 |
| repository holdout rows read / scored | 0 / 0 |
| 历史候选数 | 未读取 |
| 训练模型 / 选择参数 | 0 / 0 |
| TradingView alert / webhook / order | 0 / 0 / 0 |
| forward collector / ACTIVE 切换 | 0 / 0 |

因此 val AUC、置换检验 p、top-decile 毛/净收益、胜率、PF、单特征基线和同币×同时间块×同波动桶匹配随机对照均**不适用**；填任何数字都会把编译结果冒充经济证据。本轮同等严格的零假设合同是：出现任一未来引用、回填、非 4h 出信号、冻结常数漂移、源码哈希不一致或官方编译错误即失败。静态测试与官方编译拒绝了这些实现层失败，但它们不检验形态是否有交易价值。

## 风险与诚实声明

- 该 Pine 当前只是 research indicator，不是 `strategy()`，没有进出场、止盈止损、手续费、滑点或资金曲线。
- 因果 V1 尚未做任何历史候选审核，信号可能过密、过少或误报很高；官方编译通过不改善这一事实。
- 1–3 根线性折算门是冻结的设计假设，不是经 pre-holdout 或前向数据验证的统计最优解。
- 找币版完成形态的 RMSE、DTW、phase-scramble p 值和 8 多 + 8 空候选不能拿来给本 Pine 背书。
- 本配置继承父配置的边界后参考阈值，不能通过倒放到旧数据就恢复成独立 pre-holdout 试验；任何旧数据 replay 只能作描述性调试。
- 当前脚本已作为私有指标留在 TradingView 图表上，便于 Owner 直接查看；没有创建 alert。Owner 如不想保留，可在图表和私有脚本列表中手动移除。
- 本轮没有消费该配置的 holdout，没有变更模型、阈值预设、ACTIVE/frozen、forward log、新鲜度门或真金状态；training/production eligibility 均为 false。

## 怎么使用

1. 在 TradingView 打开任意普通蜡烛图并切到 4 小时。
2. 打开 Pine Editor，粘贴 `experiments/active/exp-btc-4h-causal-pine-v1/pine/fable_4h_ma_launch_causal_v1.pine` 全文并添加到图表。
3. 先观察绿色 `L` / 红色 `S` 与标签中的 `anchor -0/-1/-2 bar`。箭头所在位置才是程序真实知道结果的时刻。
4. 若 Owner 观察后要收提示，再手动建立 `Fable 4H causal LONG candidate` 或 `SHORT` alert，并选择每根 K 线收盘一次。
5. 不要把诊断 score 当胜率，也不要据此直接下单。

## 下一步选项

下一条合法且不新增消耗 holdout 的研究动作，是给本 V1 写完全相同语义的 Python causal replay，只在 **2026-05-04 之前**的数据上统计候选密度、逐例渲染和匹配随机对照，并先冻结经济标签与成本合同。由于阈值已继承边界后参考信息，该 replay 只能作描述性调试，不能冒充无偏验证；确认级仍需版本冻结后的真正前向样本。

任何 holdout 读取、tip-smoke、forward、Telegram/webhook 自动发送、ACTIVE 接入或实盘动作，都需要 Owner 另行明确批准；本轮没有越过这些门。

## 复现命令

以下命令只核验源码、静态合同、注册表与 HTML，不读取市场数据：

```bash
cd /Users/zhangzc/fable-trading

shasum -a 256 \
  experiments/active/exp-btc-4h-causal-pine-v1/pine/fable_4h_ma_launch_causal_v1.pine

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_btc_4h_causal_pine.py \
  tests/contracts/test_registries.py \
  tests/boundaries/test_experiment_isolation.py

python3 scripts/md_to_html.py \
  analysis/p0_btc_4h_causal_pine_v1_20260825.md \
  --out-dir analysis/html
```
