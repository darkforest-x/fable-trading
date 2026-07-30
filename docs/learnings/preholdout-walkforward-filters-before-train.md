# Pre-holdout stability must filter time before any train/score step

- **问题**：对冻结候选做 walk-forward 稳定性审计时，磁盘上的 judgment CSV 常含 holdout 行；若先训练再按时间切，等于静默消费 holdout。
- **死胡同**：直接 `load_splits` 取 val 当“历史稳定性”——val 是开发窗末端，不是多折滚动，也不能证明跨时期稳健；对 `path.resolve()` 做相对路径会因 `data/` 软链接跳出仓库。
- **有效路径**：先按 `signal_time < HOLDOUT_START - purge` 过滤源表，再切 ≥4 个时间折；训练只在 expanding pre-fold 上；任意时间戳 ≥ holdout 直接 abort。分数门槛用 train-only q90 固定规则，不回看折结果搜参。
- **通用规则**：稳定性/历史证据脚本的第一行断言是 holdout 边界，不是 metrics 表头。产物路径展示勿 `resolve()` 穿过 data 软链接。
- **牵连**：`scripts/strategy_stability_preholdout.py`、`src.judgment.train.HOLDOUT_START`、`src.backtest.run.SCORE_QUANTILE`、既有 swap/H1/H8/H10 CSV。
