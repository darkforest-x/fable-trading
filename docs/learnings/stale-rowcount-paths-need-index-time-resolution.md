# 带行数后缀的行情文件路径会过期，样本身份必须靠索引与时间闭合

- **问题**：系统重装或继续 fetch 后，`okx_SYMBOL_15m_<rows>.csv` 的行数后缀变化；1,345 行 manifest 中 1,339 条旧文件名已不存在。
- **死胡同**：把“文件不存在”当样本丢失，或只按 symbol 选当前最长文件。前者无谓损失数据，后者在起点变化、重复缓存或错币时可能把正确 SHA 外观下的错误 K 线送入评分。
- **有效路径**：把旧路径降级为谱系声明；枚举同 symbol 候选，并要求原 decision index 上的 UTC 时间与 manifest 记录完全相等。零匹配或多匹配都 fail closed，解析成功仍显式记录 `recorded_source_path_stale=true`。
- **通用规则**：文件名中的行数不是身份。时间序列样本恢复至少同时验证 symbol、位置索引、决策时间；若还有多候选，再加入源文件 SHA/起止时间，不得静默择一。
- **牵连**：`datasets/owner_short_gold_center_v1/positive_manifest.jsonl`、`data/kline_fetched/`、`resolve_symbol_source()`；本轮 1,339/1,339 stale 路径恢复，1,345/1,345 全部评分。
