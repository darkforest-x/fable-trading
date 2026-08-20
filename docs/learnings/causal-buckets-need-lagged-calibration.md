# 同月分桶不是因果分桶

- **问题**：匹配对照按整个月 ATR 排名再 `qcut`；月初交易的桶因此取决于月末尚未发生的波动，虽然特征 ATR14 本身是因果的，分层规则却不是。
- **死胡同**：把“输入特征只看过去”直接等同于“由它派生的分桶也只看过去”。全月 rank、全 split 标准化与全窗 quantile 都会重新引入未来分布。
- **有效路径**：每个 UTC 月只使用前一个完整 UTC 月的 eligible ATR 分布冻结四个切点，再给当前月逐 bar 分桶；未来突变测试必须保持此前桶值不变。
- **通用规则**：所有 rank/bin/normalization 都要写出 calibration 截止时间。若没有严格早于被评分样本的拟合窗，就不能称为 causal。
- **牵连**：`_atr_month_buckets()`、matched controls、随机对照超额与 p 值、control-sensitivity/regime/backcast 产物和报告措辞。
