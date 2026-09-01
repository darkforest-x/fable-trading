# 同一 tune 上早停并选特征组会产生不稳定赢家

- **问题**：在真实 YOLO 候选上，LONG/SHORT 各自从七个预注册特征子集选择 tune 表现最好的组合；两个赢家到了随后 April final 都由“最不差”翻成明显负收益。
- **死胡同**：把 28 个手工特征当成固定真理，或反过来认为删掉若干组自然会减少过拟合。七个方案在 tune 上扣成本后其实全部为负；从负值中挑最大值，只是在选择噪声。并且同一 tune 同时承担 LightGBM early stopping 和方案选择，乐观偏差更大。
- **有效路径**：先固定候选、标签、依赖块、时间切分、成本和模型参数，仅改变特征列；按方向在 tune 选择后冻结 receipt，再打开 final。最终用扣成本 top-decile、冻结 q90、置换检验、样本量和匹配随机对照共同否决，而不是用 AUC 或单个漂亮收益。
- **通用规则**：如果所有开发方案的主经济指标都低于零，应允许“没有赢家”；下一轮应增加目标域独立事件并拆出 early-stop 窗与 feature-selection 窗，不能继续在同一 tune 上扩大搜索空间。
- **牵连**：28 个 legacy causal features；LONG/SHORT 分训；train 417、tune 229、final 242 个独立事件；TP5/SL2/72；0.2% 往返成本；completed-history L1 候选。
