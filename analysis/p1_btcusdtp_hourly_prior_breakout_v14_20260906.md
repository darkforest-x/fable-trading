# BTC Hourly Breakout Support

## 技术摘要：结构突破筛掉很多信号，但本轮不具备预设回测支持

目标仍是找到扣费后稳定盈利的小时趋势系统，**现在还没有达成**。这轮实际完成了新的单变量检查：在原251个小时穿均线信号上，仅增加“本根收盘突破此前20小时高低点”，保留60个、排除191个，没有数据未知项；原462个对照各自判断后保留53个。

60个低于事前规定的80个，最少的半年只有11个，低于每半年12个。按照先冻结的停止规则，**这一项没有进入收益回测或收益排名**；没有读取旧交易结果表，也没有计算这60笔的盈亏。结论是“按既定标准样本不足”，不是“这60笔亏损”，也不是“信号更精准”。

本轮只用已反复研究的2023–2024历史数据；未动用2026-05-04后的holdout，未替换TradingView或实盘设置。

## 先明确分母：原信号和对照全部保留

病例是原系统的251个小时直接入场机会，而不是经过本轮挑选后的赢家。对照是原来的462个自身入场机会，分属154组三个对照；另97个病例仍未匹配，不重新分配对照。所有时间按UTC，四个半年切分，每个切分末尾保留原72小时隔离。

| 支持项 | 原规则不加本轮门 | 新增此前20小时突破 | 本轮含义 |
|---|---:|---:|---|
| 病例原机会数 | 251 | 251 | 分母没有删除 |
| 病例允许进入后续回放 | 251 | 60（23.90%） | 不是已执行60笔交易 |
| 病例已知不满足门 | 0 | 191（76.10%） | 不按缺失或亏损处理 |
| 病例数据未知 | 0 | 0 | 缺数据没有造成信号减少 |
| 对照原机会数 | 462 | 462 | 使用每个对照自己的高低点 |
| 对照允许进入后续回放 | 462 | 53（11.47%） | 不继承病例的通过状态 |
| 固定完整对照组 | 154 | 154 | 弃单也不删组、不重配 |

这些都是入场支持数量，没有收益或成功率含义。对照保留比例低于病例，可能与病例本来就是穿均线大实体/吞没有关，不能把比例差称为超额收益。

## 四个半年都有机会，但总量与最弱半年未过门

<!-- SOURCE: v14_counts -->

四个半年的病例支持分别为17/55、15/66、11/55、17/75。问题不是只集中在一个月：23个月有信号，每个半年至少5个月有信号；问题是加入硬过滤后总机会过少，2024上半年也未达到预设最低数量。

下图保留各期原分母、弃单和未知字段，零点起轴，仅比较支持数量，不连接成收益曲线。

<!-- prior-breakout-support-chart -->

## 这次到底新增了什么规则

本轮新增门只有一条：做多时，K1收盘严格高于此前20根完整小时K线的最高价；做空时，K1收盘严格低于此前20根完整小时K线的最低价。等于边界也不算突破。

K1开盘时刻记作S；比较区间是S−20小时至S−1小时的小时开盘，**不包括K1本身**。区间高低点在S已可知，K1收盘在S+1小时才可知。必须20小时连续完整，并独立核对本根收盘与保存的信号收盘一致。任一必要来源不足应标未知，不能当成失败或无交易收益0。

原母信号仍是SMA40（HL2）实体严格穿越，当前HL2颜色与方向一致。形态分支二选一：大实体分支要求实体占比至少0.65且振幅至少1ATR；吞没分支要求真实吞没且振幅至少0.65ATR，不额外强加大实体分支的0.65占比。两者都要求方向收盘位置至少0.7。不叠加V13的4小时颜色门，不加小时斜率门，也不优化20这个窗口。完整参数见[冻结入口配置](../../experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json)。

原执行规格保留为下一阶段的固定基线：确认小时收盘后下一真实5分钟开盘入场；K1极值硬止损；最多72小时；假设往返20bp成本；完整5分钟SMA40颜色从持仓方向变成反方向后按因果顺序退出。**本轮并未执行这些交易模拟**，也没有把无交易标成“收益0”。

## 通过与未通过：两项样本门失败，两项月份门通过

预设“至少80个、每半年至少12个”是沿用的研究最低支持，不是统计功效计算，也不表示80个就足以证明盈利。

| 事前门槛 | 实际支持 | 判断 |
|---|---:|---|
| 病例至少80个 | 60 | 未通过 |
| 每个半年至少12个 | 最少11个 | 未通过 |
| 至少12个活跃月 | 23个 | 通过 |
| 每个半年至少3个活跃月 | 最少5个 | 通过 |

多头28/130通过，空头32/121通过，两边都明显减少，并非只把某一方向全部关闭。2024年2月没有通过病例，其余月份保留在完整24个月统计中，没有删掉空月。

原匹配覆盖仍是154/251=61.35%，达不到原90%要求，这是另一个未解决的验收问题；不能因为本次713条门状态都已知，就声称251个病例都有完整对照。即使四项支持门全部通过，也只能进入下一阶段，不能自动获准盈利、上线或推广。

## 独立核对：逐条高低点可以复算，不用相信标签

实际保存了713条完整机会记录、14973条自身小时来源（每条机会20根历史加1根K1），以及62行分维度数量统计、154行固定对照支持。病例的新门与原母表事前保存的breakout20标记全部一致；这是同一原始数据上的实现一致性，不是独立市场验证。

独立校验已通过：从保存的小时来源重新核对窗口、连续性、高低点、本根收盘、严格大小关系、未知状态、数量分母与匹配身份，并核对4份输出哈希和11份提交源码收据。将K1误放入“此前区间”、把边界相等当通过、把对照套用病例门、删除空月或未知行，均由负例测试检查。原始5分钟到小时的聚合另由合成测试与源码收据约束；保存表校验不冒称又独立重放了原始行情。

因果窗口实现依据已安装pandas 2.3.3的[移位规则](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.shift.html)与[滚动窗口规则](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.rolling.html)。20个观测本身不保证连续20小时，时间连续性另外检查。

## 为什么没有胜率、PF和“调好了”的结论

本轮是非收益支持审计，val AUC、排序置换p、top-decile毛/净收益、胜率、PF、单特征收益基线以及同成本随机入场收益均不适用：没有新计算或读取交易结果。原匹配对照只用于检验支持分布，不替代经济对照。

同等严格的实现反证是独立来源重算与合成负例变异，检验的是“门有没有算对”，不是“门能否赚钱”。实测没有缺失值不等于市场没有风险；所有60个仍可能盈利、亏损或受到成本侵蚀，本轮对此没有结论。

## 风险与诚实声明

这是反复使用2023–2024样本后的探索，没有新鲜样本外盈利证据。此前V1也试过breakout20，但当时叠加了已选小时斜率且出口语义不同；旧失败保留，本轮只是减少混杂后的支持复核，不把旧参数包装成新发现。

OHLCV实际物化219551行，最后到2024-12-31 23:55 UTC；归档更晚时间仅用于时间戳/哈希边界检查。holdout消耗0，没有训练、依赖升级、生产参数切换、TradingView更新或真金动作。

附带笔记本的3格代码已按顺序用普通Python执行，并复用固定的同一个校验器；它不是第二套独立验证，也没有运行Jupyter内核或完整nbformat校验。HTML已通过官方打包与结构校验；本机无可用Chromium，手机布局和来源弹窗的实际浏览器检查未完成。

这轮不能回答留下60个的净收益是否改善。预先选择不继续评价，是为了不在样本失败后用收益救参数；不是保证“不做的那些交易就一定差”。

## 下一步：回到退出周期是否切断趋势的问题

不降低支持门，不在这条分支上继续搜索10/15/30小时窗口。另行核查原251机会是否已做过“只把5分钟真实变色退出改为15分钟真实变色退出”的同母体对比；先去重，再冻结独立实验。过去其他人群上做过15分钟管理，不能直接当作这251个机会的答案。

必须区分“在旧退出前没有走到1R”和“之后也没有趋势”。前者是已有持仓路径统计，不能据此断定延后退出无效。同样，延后退出也可能扩大止损或回吐；只有保持相同入口、硬止损、费用和机会分母的对照回放才能检验。盈利目标继续，当前没有接受或上线候选。

## 复现与保存证据

在本仓已有依赖环境执行。首次价格运行前builder/config/plan/tests已提交为5fc542b；首次检查完成于2026-09-06 09:43 UTC。结果目录不可覆盖；从无本轮产物的工作副本复现首次运行，或只运行保存证据校验，不删除已有实验结果。

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_prior_breakout_research
.venv/bin/python scripts/verify_hourly_impulse_prior_breakout_v14.py
.venv/bin/python -m pytest -q tests/test_hourly_impulse_prior_breakout*.py tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py
```

生成交付物的命令如下；notebook和artifact输出须为新路径，不覆盖已有研究证据。HTML是由同一份完整artifact生成的展示副本。

```bash
experiment=experiments/active/exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14
report=analysis/p1_btcusdtp_hourly_prior_breakout_v14_20260906.md
.venv/bin/python -m yoyo.evaluation.hourly_impulse_prior_breakout_notebook --output "$experiment/prior_breakout_audit.ipynb" --check
.venv/bin/python scripts/md_to_html.py "$report" --out-dir analysis/html
.venv/bin/python -m yoyo.evaluation.hourly_impulse_prior_breakout_report --markdown "$report" --summary "$experiment/results/summary.json" --counts "$experiment/results/counts.csv" --output "$experiment/artifact.json"
node "${CODEX_HOME:-$HOME/.codex}/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs" --input "$experiment/artifact.json" --output analysis/html/p1_btcusdtp_hourly_prior_breakout_v14_20260906.html
```

支持源表和运行收据位于本轮实验results目录。下列附件链接按交付HTML所在的analysis/html目录解析，依赖本仓文件存在；HTML正文和图表数据本身为自包含。

- [支持审计笔记本](../../experiments/active/exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14/prior_breakout_audit.ipynb)
- [完整验证记录](../../experiments/active/exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14/VERIFICATION.md)
- [逐条入场门](../../experiments/active/exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14/results/entry_context.csv)
- [逐请求小时来源](../../experiments/active/exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14/results/prior_hourly_rows.csv)
