# 排名尾部变好不代表冻结 q90 执行门也变好

- **问题**：在同一批真实 YOLO 候选上，新增因果特征把 April 精确 top-10% 净收益从
  `+0.898%` 提到 `+1.108%`，冻结 q90 入选数从 20 增到 31，且 8/8 匹配随机对照均为正；
  但冻结 q90 的单位净收益从 `+1.231%` 降到 `+0.987%`，置换检验也只有 `p=0.046`。
- **死胡同**：只看更高的 rank-tail 均值、更多信号或匹配对照全胜就宣布“加特征成功”；
  这些指标没有回答实际 tune-q90 门在新时间段是否比原门更赚钱，也无法代替预注册显著性门。
- **有效路径**：先只在 tune 选择 LONG/SHORT 特征组并提交 selection receipt，再一次性打开
  final；同表同时比较 tie-aware top-decile、冻结 tune-q90、方向最小样本、匹配对照和置换 p。
  增量模型只通过部分门时，结论写成“有增量迹象但 REJECT”，不得用 final 反向改阈值或重选组。
- **通用规则**：任何会改变分数分布的特征增量都必须把“排序尾部质量”和“冻结执行阈值质量”
  当成两个独立问题。top-decile 变好只能支持继续收集新时间样本，不能自动替换旧模型或旧阈值。
- **牵连**：`scripts/research_15m_ma_launch_l2_feature_addition.py`、
  `analysis/p3_15m_ma_launch_l2_feature_addition_20260902.md`、
  `exp-15m-ma-launch-l2-feature-addition-v1`；成本 0.2%、TP5/SL2/72、holdout 未读取。
