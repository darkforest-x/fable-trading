# V11.2 入场推进上限单变量实验

结果：三个周期均未通过冻结的研究筛查。15m/1h全期有所改善，但1h后段仍亏，4h后段恶化；不修改正式策略。只有一个阈值E<=1，没有参数搜索。

- 综合解读：`analysis/p1_spike_v112_entry_extension_20260920.md`
- 单变量与统计门：`PROJECT_PLAN.md`
- 图：`comparison.png`，来源回执`figure_receipt.json`
- 全期/早段/后段/跨界、原/新与随机对照：`statistics/summary.csv`
- 均值差与95%月块区间：`statistics/differences.csv`
- 571个原始候选及过滤/占仓状态：`statistics/decisions.csv.gz`
- 原570笔+新450笔完整账本和匹配控制：`statistics/trades.csv.gz`
- 120筆被过滤交易：`statistics/changed_trades.csv`（新增成交0）
- 避亏/漏盈及损益守恒：`statistics/attribution.csv`
- E与Q6固定分桶及随机对照：`statistics/feature_groups.csv`；Q6不参与本轮入场
- 最大赢家敏感性：`statistics/tail_sensitivity.csv`
- 拒绝原因逐门布尔值：`statistics/verdict.csv`
- 原规则及共享交易逐字段复现：`statistics/parity.csv`
- 按币原始输出：`run/streams/`；输入身份`run/identity.json`；完整回执`completion.json`
- 独立Luna Max复核：`independent_review.md`；最终交付SHA：`delivery_manifest.json`

原始数据是Binance2024-09-10至2026-05-01前的旧档案，不是当前OKX/TV实时记录；29币是Owner指定历史子集。2笔4h边界持仓不参与收益，450入场只有448已平仓。R和bp不能互换，也不是账户收益。

复现命令见综合报告。无HTML；无训练、生产准入或实盘动作。
