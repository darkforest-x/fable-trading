# 障碍标签胜率不等于扣成本后的盈利比例

- **问题**：固定 TP/SL/timeout 标签账本里的 `win_rate` 是 `label == 1` 的比例；在当前障碍解析器中它表示先到 TP，而 timeout 即使最终收益为正也仍可能是负标签。把 13/31 的标签正例率直接回答成“13 个盈利”会低估真实净盈利数。
- **死胡同**：只读取汇总 receipt 的 `frozen_q90.win_rate` 并按普通交易胜率解释。这个字段没有携带“TP 标签率”还是“净收益大于零”的语义，且没有展开 timeout，导致把 13 TP、14 SL、4 timeout 错写成 13 盈利、18 亏损。
- **有效路径**：从冻结 score ledger 先取 `dependency_representative && selected_keep`，再按 `episode_id` 一对一联结原 outcome ledger；同时统计 `outcome`、`label`、`realized_ret` 和扣成本后的 `net_ret > 0`。本例得到 13 TP、14 SL、4 timeout，其中 3 个 timeout 扣成本后为正，因此真实口径是 16 个净盈利、15 个净亏损。
- **通用规则**：凡报告 `win_rate`，第一步先追到标签定义；固定障碍任务至少并列报告 `TP 标签率`、`TP/SL/timeout` 数量、`net_ret > 0` 比例和成本。用户问“赚钱吗”时只用 `net_ret > 0` 回答，不能用分类标签代替。
- **牵连**：`yoyo/layers/l2_judgment/labeling.py`、`experiments/active/exp-15m-ma-launch-l2-feature-addition-v1/results/training_receipt.json`、`analysis/output/ma_launch_l2_feature_addition_v1/final_validation_feature_addition_scored.csv`、`scripts/render_15m_ma_launch_l2_feature_addition_signals.py`；成本固定为 0.2%，信号统计必须先做 dependency-block 去重。
