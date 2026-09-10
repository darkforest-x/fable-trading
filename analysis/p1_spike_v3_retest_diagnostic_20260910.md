# SPIKE：下一根回到区间内，究竟删掉了什么

## 技术摘要

**F 的“一根后仍站在突破边界上方”主要删掉了回到原区间内的候选，而不只是跌破整个结构的候选。** 全部 10386 个原 V3 候选中，6942 个下一根收盘仍在原区间上方，3419 个回到区间内，25 个跌到区间下方。本次没有未知裁决。F 拒绝的 3444 个候选中，99.27% 属于“区间内”。

原 V3 及时捕获的 1463 个旧正例中，有 292 个下一根回到原区间内，其中 155 个属于原大幅正例。这说明简单排除这类回落会丢失后来达到旧标签目标的启动；**它不证明所有区间内回落都是成功回踩，更没有证明放行它们能盈利。** F 的原验收结果仍为四门全部未通过，本次不改变信号、过滤、收益或分母。

当前 TradingView 已保存的是 **“SPIKE V4 · 确认信号”**，默认只显示真实确认信号，首个确认收盘开启空闲后的风险参考。原确认逻辑未变，风险参考起点已与早期预警版不同；这项显示交付没有使用本次回踩分类，也没有完成新的收益验证。具体范围见[原生保存回执](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-layered-display-20260910-v1/qa/v4_confirmed_only_native_save.json)。

## 先区分三个分母

本诊断复用固定 278 个 OKX 山寨合约的 1H、多头历史，不含 BTC/ETH。研究时钟为 UTC 2026 年 7 月 10 日至 9 月 9 日之前，共 61 天；源数据含预热共 837239 根。没有新抓行情，也没有按结果重新挑币。

| 统计对象 | 固定数量 | 这里实际在数什么 |
|---|---:|---|
| 原 V3 父候选 | 10386 | 原指标在真实收盘时发现的预警；同一趋势可以有多个候选 |
| 研究内标签锚点 | 8046 | 1660 正例、6240 负例、146 未知；原 14904 源标签保持不变 |
| 原及时正命中 | 1463 | 原 V3 在正例锚点当根或下一根出现预警；不是盈利交易数 |
| 原大幅正例 | 947 | 原正例中，未来 24 根最高价相对锚点收盘至少上涨 8% 的子组 |

旧正例的定义是未来 24 根收盘先达到 +4 ATR，而不是先达到 −2 ATR。这些未来条件仅用于标签和复盘，不能在信号当时提前知道。大幅正例同时要求先满足旧正例定义，不能只凭后面出现一根高价就算成功。

候选、标签锚点、研究交易事件是不同的计数单位。跨币同一晚行情、同币连续锚点和同趋势多次信号存在关联；即使表中一行对应一项记录，也不能将它们当作相互独立的实盘交易。本次不计算“区间内胜率”或分类收益。

## 被拒绝的候选中，99.27% 只是回到原区间内

原候选出现时，固定其前 12 根的最高价和最低价。下一根裁决价严格高于原高点记为上方，低于原低点记为下方；等于任一边界都属于区间内。下一根只提供已冻结的裁决价，区间不会跟着它滚动。

| 下一根收盘位置 | 原候选数 | 占全部 10386 候选 | 原 F 裁决 |
|---|---:|---:|---|
| 原区间上方 | 6942 | 66.84% | 接受 |
| 原区间内，含两端相等 | 3419 | 32.92% | 拒绝 |
| 原区间下方 | 25 | 0.24% | 拒绝 |
| 未知 | 0 | 0.00% | 保留未知，不当作拒绝 |

F 的原条件只要求下一根收盘高于原突破边界。因此，跌回区间内与跌破区间低点都会被拒绝。本次把这两种价格位置拆开，发现前者占拒绝项的 3419/3444。**这个分布提示原 F 的拒绝含义比“整个结构失效”更宽。** 它仍不能回答这 3419 个候选中哪些回落可接受、哪些会继续恶化。

下面三个面板分别使用全部候选、原及时正例、全部大幅正例作为分母，不能横向直接把比例称为胜率。右侧明确保留原 V3 没有及时捕获的 149 个大幅正例，未将它们删除以改善比例。

![原候选、及时正例、大幅正例的下一根位置分布](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/figures/retest_position_counts.png)

来源：[原位置分区](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/position_summary.csv)、[原 F 裁决交叉表](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/f_status_crosstab.csv)。本次裁决未知为 0，与旧标签中的 146 个未知是两个不同概念。

## 292 个原及时正例会被这一根回落排除

原 1463 个及时正例中，1171 个下一根仍在区间上方，292 个回到区间内。后者占原及时正例的 19.96%，其中 155 个属于大幅正例；没有原及时正例被分到区间下方或未知。

| 下一根位置／原捕获状态 | 原及时正例数 | 占原 1463 及时正例 | 原大幅正例数 | 占全部 947 大幅正例 |
|---|---:|---:|---:|---:|
| 原区间上方 | 1171 | 80.04% | 643 | 67.90% |
| 原区间内 | 292 | 19.96% | 155 | 16.37% |
| 原区间下方 | 0 | 0.00% | 0 | 0.00% |
| 未知 | 0 | 0.00% | 0 | 0.00% |
| 原 V3 未及时捕获 | 不属于此分母 | 不适用 | 149 | 15.73% |

**这些 292 个是事后已知的旧正例，不是事前可辨认的“好回踩”。** 其余区间内候选也不能直接当作负例或亏损交易：标签有自己的锚点和间隔规则，并没有给每个父候选提供独立完整的交易盈亏标签。不能用 292/3419 推出某种回踩策略的胜率。

全部 947 个大幅正例仍是 643＋155＋149。不能把旧信号覆盖、等待期间的后来走势或原已持有参考加入新的及时捕获分子，也不能因为发现这 155 个漏报就修正 F 原失败结果。来源：[完整锚点联结](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/joined_anchors.csv.gz)、[正例位置统计](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/timely_positive_positions.csv)。

## 三个固定案例都回到原区间内，没有收盘跌破原低点

案例在分类前已固定：HYPE、PEPE 北京时间 8 月 19 日 21:00 的原候选，以及 NEAR 同日 15:00 的原候选。下图只连接原候选收盘与下一根实际收盘，不是 K 线图，也不表达盘中走法；后续走势仍在原 F 的 96 根全局图中单独展示。

### HYPE：下一根 58.637，仍高于原低 58.023

原候选 21:00 收盘为 59.279，原区间为 58.023—58.955。22:00 收盘降到 58.637，所以 F 以“不高于原高点”为由拒绝；价格位置属于原区间内。这里看到的是一次边界回落，而不是收盘跌破整个原区间。后面能否走成趋势不能由这两个点确定。

![HYPE 原候选与下一根的冻结区间位置](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/figures/HYPE_next_close_position.png)

查看[HYPE 原 96 根全局图](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/hype.png)。原 V3 后续在 8 月 20 日 00:00 出现质量确认，但原 F 不允许已拒绝父被后来的确认复活；图上不得把这个确认填回 21:00。

### NEAR：下一根 1.611，处于原区间 1.562—1.616

原候选 15:00 收盘为 1.620，16:00 收盘为 1.611。它跌回原高 1.616 下方，却没有收盘跌破原低 1.562，因此也被归为区间内并被 F 拒绝。这个候选不能因后续出现另一个启动而改写原裁决。

![NEAR 原候选与下一根的冻结区间位置](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/figures/NEAR_next_close_position.png)

查看[NEAR 原 96 根全局图](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/near.png)。原报告记录次日 05:00 出现另一个父候选，06:00 被 F 接受并公开确认；它属于新的原候选，不是前一天 15:00 候选的及时挽回。

### PEPE：原同根质量确认，也没有改变下一根的拒绝

原候选 21:00 收盘为 0.000002616，原区间为 0.000002566—0.000002606。22:00 收盘为 0.000002596，回到区间内。原 V3 在候选当根已经给出质量确认，但 F 的独立下一根价格条件仍拒绝该父；原确认和 F 接受不是同一个事件。

![PEPE 原候选与下一根的冻结区间位置](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/figures/PEPE_next_close_position.png)

查看[PEPE 原 96 根全局图](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results/figures/pepe.png)。这些案例用于解释已冻结的拒绝机制，不能代替全池评估，也没有重新挑选最有利的截图。原案例价位见[固定案例表](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/fixed_examples.csv)。

## 原 V1／V3／F 的结果保持原样

以下直接沿用[已冻结 F 报告](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_price_acceptance_20260910.html)的全期比较，不是本诊断新增回测。F 接受父按实际公开时钟计数为 6940；本次按原发现时钟分组有 6942 个接受候选，两者差异来自原研究边界的 1 个流入、3 个流出，不能混成同一分母。

| 版本 | 原信号／公开父 | 公开确认升级 | 同根合并标签 | 标签减少 | 1660 正例及时召回 | 947 大幅召回 | 原 V3 命中保留 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 原版 V1，历史对照 | 86 | 不适用 | 86 | 不适用 | 0.84% | 1.16% | 不适用 |
| 原 V3 | 10386 | 3809 | 12042 | 0.00% | 88.13% | 84.27% | 100.00% |
| F：下一根价格接受 | 6940 | 3171 | 7386 | 38.66% | 70.54% | 67.90% | 80.04% |

| 版本 | 有效研究交易事件 | 全部事件净胜率 | 自然退出净胜率 | 平均净收益 bp | 匹配事件 bp | 随机对照 bp | 匹配净超额 bp | 置换 p／六假设 Holm p |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 原版 V1，历史对照 | 86 | 47.67% | 47.44% | 643.95 | 683.99 | 279.80 | 404.20 | 0.08299／不适用 |
| 原 V3 | 10386 | 29.50% | 27.59% | 25.01 | 23.68 | 123.92 | −100.24 | 1.00000／不适用 |
| F：下一根价格接受 | 6940 | 30.43% | 28.30% | 33.82 | 29.03 | 143.98 | −114.95 | 1.00000／1.00000 |

研究交易事件按父信号独立评估、可能时间重叠，不代表独立样本或可同时执行的账户组合。经济沿用原下一根开盘、固定风险和退出规则、20 bp 往返成本；F 用接受根之后的开盘，不能沿用原候选的更早成交价。原 V3/F 共用本轮因果匹配日程；原 V1 保留历史随机对照，不冒称与本轮采用同一套随机时钟。没有新账户净值或最大回撤结果。

F 仍然没有满足：合并标签减少至少 50%且不超过 6021、保留至少 90% 原及时正例、大幅召回至少 80%、正匹配净超额且六假设 Holm p<0.01。已有 13 项单门和 A—F 顺序探索的失败记录不因本描述而改变。上表也不能将原 V1 较高的均值解释成已验证最优：它只有 86 个研究交易事件，原置换 p=0.08299，捕获率也很低。

## 如何保证没有把下一根的信息回填到原信号

分类和标签联结分两次执行。第一步只读取原候选身份、原 t 的冻结区间与 F 已保存的下一根裁决，保存完整 10386 条分类后冻结 SHA；第二步才读取旧标签，使用原 `event_i + first_signal_lag` 找到真实候选 t。不能用 F 接受根替代原信号根，也不能拿下一根滚动的低点重画区间。

独立审核通过：认证 1815 个不同文件，核对 10386 条原 V3 事件，独立重建原区间的 249264 个 OHLC 边界值，检查 128736 个源标签单元格；全部 8046 研究锚点和原 1463／947 分母守恒。34 项合成测试包括边界相等、未知、原低点冻结、错位身份、时间篡改、缺行、哈希篡改和拒绝覆盖。实际审核和图表核验见[独立结果回执](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/qa/independent_result_review.json)。

这是非交易诊断，本次新增单特征 AUC、训练 val AUC、top-decile 毛／净收益、分类胜率、匹配随机超额和置换 p **均不适用**，没有编造或填零。完整性阴性对照是故意错移身份、边界、时钟或改动哈希后，程序必须拒绝；它检验资料联结是否正确，不证明预测能力。原交易指标及单特征描述保留在旧 F 报告中。

## 风险与诚实声明

- 这里复用已经反复看过、且获准使用全部日期的历史。它是固定 F 配置第 1 次评分之后的描述性重读，不是新配置评分、盲样本外验证或新增独立证据。
- 原及时正例是按未来标签筛出的子集；不能从其区间位置分布，倒推出某个实时回踩规则的精度、胜率或净收益。
- 下一根收盘跌破原区间低点只有 25 例，且其中没有原及时正命中，不足以证明“只过滤这 25 个就好”。这不是已测试的新过滤器，也不能解决需要大幅降低提示数量的目标。
- 候选当根无法提前知道下一根裁决。任何后续规则都必须保留真实确认时钟、等待导致的价格差异和交易成本，不能回填箭头。
- 61 天、278 个 OKX 山寨合约的多头 1H，不能代表三年、BTC/ETH、其他周期、空头或全部交易所。案例图中的后续行情只用于复盘。
- 本报告不改 V4、通知、监控、ACTIVE 或实盘。V4 已完成的是当前确认显示和风险参考起点的交付，不代表已有市场收益验证；本诊断更不是要部署 F 过滤。

## 下一步：先定义同一段结构，再研究去重

这轮证据支持先把“回到原区间内”和“跌破原区间低点”分开记录。更值得继续研究的是：**同一个蓄势结构在尚未结束时重复出确认，是否应归为同段升级；出现新的蓄势结构后，才重新记为新的启动。** 这比继续用一次回落直接删信号，更贴近用户想减少反复开单提示的目的，但它目前仍是待验证方向。

下一轮应先冻结因果的结构身份、失效与重新蓄势条件，再做单一变量检验。要同时报告原始发现数、独立结构启动数、同段升级数、漏掉的真正新结构，以及实际时钟下的原执行／随机对照；不能把已持有覆盖拿来补新的及时捕获率，也不能只压缩图上的标签就宣称盈利改善。

仍需回答的两个问题是：区间内回落中哪些属于同一结构延续，哪些已经失效；旧结构结束后，如何及时承认一次真正的新结构。答案必须来自事前可用的结构定义与独立验证，本报告不挑阈值、不自动上线新规则。

## 复现与证据

原运行先提交 builder、tests、计划与实际预审，再分类、冻结分类 SHA、联结旧标签，最后渲染图表。以下是本次原命令；默认结果已完成，重复执行会拒绝覆盖。重做时需选择全新的 `--output` 目录，并用新生成的分类／联结 SHA，不得删除原证据或照抄旧 SHA 给新目录。

```bash
.venv/bin/python -m pytest -q tests/test_spike_burst_v3_retest_diagnostic.py
.venv/bin/python -m yoyo.evaluation.spike_burst_v3_retest_diagnostic classify
.venv/bin/python -m yoyo.evaluation.spike_burst_v3_retest_diagnostic join --classification-sha aaff96c3af614b65452e3cabca3609c3732d7f1f3fbe54d46667eebedf312f89
.venv/bin/python -m yoyo.evaluation.spike_burst_v3_retest_report --joined-sha 184aff26411b4649095d1a85558e021367b182efcecda7fa64cfc275480159fd
.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v3_retest_diagnostic_20260910.md --out-dir analysis/html
```

| 冻结证据 | SHA-256 |
|---|---|
| 原 F prepared | `32abdf5316732247e4912cd17e3c7c94353ba6756a33df38c007fd7933d83a42` |
| 原 F validation | `a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570` |
| 分类 manifest | `aaff96c3af614b65452e3cabca3609c3732d7f1f3fbe54d46667eebedf312f89` |
| 联结 manifest | `184aff26411b4649095d1a85558e021367b182efcecda7fa64cfc275480159fd` |
| 图表／片段 manifest | `c24fceeefce926a50b4e24ca3fc44017f539df34f9bbc194f259ca30315eae90` |
| 独立结果回执 | `2296c81765188570a062ad60e2575b4f14133f1b348b46d013cd42452642858b` |

完整资料：[事前计划](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/PROJECT_PLAN.md)、[预审](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/qa/preflight_review.json)、[分类 manifest](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/classification_manifest.json)、[联结 manifest](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/joined_manifest.json)、[图表 manifest](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/retest_report_manifest.json)、[全部原候选分类](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1/results/classified_candidates.csv.gz)。
