# 未预热特征要记为 unavailable，不能补值凑齐全量

- **问题**：对冻结的 26,874 个负样本重算六均线框时，70 个样本早于 SMA120 完整预热，另 2 个固定 W20 越过源文件边界；正式构建因此 fail-closed。
- **死胡同**：用前值/零补均线、临时缩短 W20、换一个附近负样本或静默跳过，都能让构建“成功”，却改变了冻结身份和密集度分布；事后只报 26,802 会让人误以为 72 个从未存在。
- **有效路径**：先让首次构建停止，再全量枚举不可定义原因；在重跑前提交修订说明。有效样本进入同口径统计，不可定义样本逐条保留 identity/reason，最终同时验 `valid + unavailable = frozen manifest`，不做补值与替换。
- **通用规则**：冻结数据上的派生特征若数学上未定义，第一步是建立 availability ledger，而不是选择填充值；报告必须同时给身份总分母、有效统计分母和按原因缺失数，并用账目等式做 QA。
- **牵连**：`exp-15m-ma-launch-ma-box-review50-v1/amendment_20260827.json`、`unavailable_negative_audit.jsonl`、SMA120 warmup、固定 W20、holdout 禁读、不可覆盖构建器。
