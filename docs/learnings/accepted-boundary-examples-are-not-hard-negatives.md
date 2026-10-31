# Accepted boundary examples must not become hard negatives

- **问题**：综合质量分筛出的部分反转存在横盘、回撤或延迟启动，自动审计给出46个复核提示；Owner看过其中6张后回复“都算”。
- **死胡同**：把审计提示当作必要条件，直接删掉所有慢启动或回撤较大的样本，会把Owner认可的形态改成研究者偏好的理想走势。模型失败也不能成为反向改标的理由。
- **有效路径**：将6张具体图记为Owner确认正例，其余样本不升级确认身份；原2256自动标签不变。只把连续干净度回归改成现有正例二分类，保持24特征、参数、权重与时间切分，独立检验“达到标准”和“极其漂亮”的目标差异。结果26.53%高分命中低于原29.08%、匹配随机30.06%，配对改善区间跨零，保留失败结果。
- **通用规则**：人审可以确认原标签而不改变数值标签。协议或局部样本确认不等于全量金标；训练目标更贴近描述，也不自动意味着具有预测能力。标签、训练目标和信号时信息分别检验。
- **牵连**：`experiments/active/exp-clean-reversal-audit-20261101-v1/owner_confirmation_20261101.json`；`exp-clean-reversal-classifier-20261101-v1`；无费用/PnL问题、无服务或准入变更。前项审计见 `composite-label-cutoffs-do-not-enforce-each-shape-requirement.md`。
