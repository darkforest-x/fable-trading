# Post-val canary 必须在读取层物理截断 holdout

- **问题**：hard-negative 重训后需要先看连续市场密度，但直接复用最近两天会再次消耗 holdout；整表读入后再筛选历史日期同样已经读取了 holdout 行。
- **死胡同**：用普通 `read_csv` 打开包含未来数据的完整 K 线文件，再按 `open_time < holdout_start` 过滤。输出虽然没有未来行，读取过程却已违反 holdout 纪律。
- **有效路径**：冻结在 val 最后一根之后、holdout 边界之前的 canary；从原连续序列索引推导目标行号，用有界 `nrows` 只物理读取所需前缀，并在产物中记录 `max_materialized_time` 与 `holdout_rows_materialized=0`。
- **通用规则**：任何 pre-holdout 评估先审计“最多读取到哪一行”，再审计“最后输出了哪些行”；输出过滤不能替代输入边界。
- **牵连**：`scripts/backtest_owner_short_gold_center_recent.py`、Owner-short 连续窗口密度、固定 val 结束时间、2026-05-04 holdout 边界。
