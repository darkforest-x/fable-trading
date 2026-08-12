# Pre-holdout 不等于 decision-causal，整张快照后切片仍读取了样本未来

- **问题**：边界诊断计算只使用 `frame[:decision+1]`，但 loader 先把整张 pre-holdout snapshot 读进内存；因此没有碰 holdout，却无法诚实声称“future OHLC rows read = 0”。
- **死胡同**：只审计特征公式是否引用 decision 后的行，或把“全部都早于 holdout”当作样本级无前视。输出切片正确不代表输入物化边界正确。
- **有效路径**：把 decision index 传进 CSV loader，直接用 `nrows=decision+1` 物理读取前缀；审计记录 `csv_rows_requested`、`rows_materialized`、`max_materialized_time`，并用 decision 后放置无效 poison row 的测试证明 loader 不会触碰它。
- **通用规则**：无前视要分两层验收：研究边界（不越 holdout）和样本边界（不越 decision）。两层都必须在读取入口限界，不能依赖读取后的 DataFrame 切片。
- **牵连**：`scripts/diagnose_local_signal_v2_semantic_boundary.py`、`source_read_audit.jsonl`、`future_ohlc_rows_read=0` 声明；延续 [post-val canary 必须前缀读取](post-val-pre-holdout-canary-must-be-prefix-bounded.md)。
