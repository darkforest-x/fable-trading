# 冻结图册可恢复无歧义索引时间

- **问题**：一份已由 ledger evidence 锁定哈希的 0G OHLC CSV，名义 `time` 列全空，初次图册构建因此拒绝其时间线。
- **死胡同**：把它当作缺失数据并换源或重抓公开 OHLC，会改变证据来源；改写 CSV 则会让 ledger 中的冻结哈希失效。
- **有效路径**：先核对同文件 `Unnamed: 0`：仅当名义时间列全空、索引列全可解析、严格递增且无重复时，读取这个已存在的时间字段，并把字段来源写进 provenance；任意部分有效或两列竞争仍失败关闭。
- **通用规则**：冻结 CSV 时间字段看似损坏时，先检查是否存在单一、完整、可验证的原始索引时间列；恢复读取语义可以接受，重写冻结字节或替换来源不可以。
- **截图边界**：查看器已加载全部冻结图数据不等于默认截图视窗包含远期账本位置；当近景窗口不足时，必须切到全局视图并在渲染收据记录该视图。
- **牵连**：`yoyo/evaluation/spike_v1_review_data.py`、`tests/test_spike_v1_review_data.py`、`experiments/active/exp-spike-v1-okx-133-review-20260911/data/repair_0g_receipt.json`。
