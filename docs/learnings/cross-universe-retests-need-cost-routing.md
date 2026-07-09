# 跨宇宙复测要先路由成本口径

- **问题**：R1' 要在 spot 与 SWAP 两个池上复测 H9。H9 过滤逻辑本身相同，但 maker/taker 成本不同；若脚本写死 spot 成本，SWAP 净值会被系统性多扣。
- **死胡同**：先把 `h9_trend_filter.py` 参数化后直接跑 SWAP，输出显示 top-bucket 净@maker 为负。复查后发现不是 H9 失效，而是脚本仍用 spot maker 0.16%，SWAP 应用 0.06%。
- **有效路径**：把成本判断集中成 `maker_cost_for_dataset` / `taker_cost_for_dataset`，按数据路径识别 `swap_replication`/`swap`，再让 H9 top-bucket、maker 组合模拟、特征版重训共用该口径。
- **通用规则**：任何 spot→SWAP 或 SWAP→spot 的复测，第一步先审成本、资金费和成交模型是否随宇宙切换，而不是先解释收益变化。
- **牵连**：`scripts/h9_trend_filter.py`、`scripts/h9_feature_retrain.py`、`src/backtest/maker_val_sim.py`、`analysis/output/h9_swap_trend_filter.json`；未改变阈值、障碍参数或 holdout 纪律。
