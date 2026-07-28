# 附录 B:实验结果汇总

从 `analysis/output/` 的 197 个 JSON 抽取。每条都是脚本当时打印的结果,不是事后重算。
其中 28 个带明确判读。


## `ab_rules_metrics.json`

| 指标 | 值 |
|---|---|
| dataset | data/swap_replication/swap_tp5_sl2.csv |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 6027 |
| splits.val.n | 1510 |
| splits.holdout.n | 1709 |
| best_iteration | 25 |
| val.n | 1510 |
| val.positive_rate | 0.3212 |
| val.roc_auc | 0.5601 |
| val.pr_auc | 0.3572 |
| val.top_decile.n | 151 |

## `ab_yolo_metrics.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap.csv |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 1382 |
| splits.val.n | 349 |
| splits.holdout.n | 640 |
| best_iteration | 67 |
| val.n | 349 |
| val.positive_rate | 0.4212 |
| val.roc_auc | 0.8172 |
| val.pr_auc | 0.7157 |
| val.top_decile.n | 34 |

## `all_ma_breakout_rule.json`

| 指标 | 值 |
|---|---|
| exit | TP3/SL1 |
| rule_dense_universe.long(above_all).n | 5474 |
| rule_dense_universe.long(above_all).win | 0.275 |
| rule_dense_universe.long(above_all).PF | 0.777 |
| rule_dense_universe.long(above_all).mean_bps | -16.1 |
| rule_dense_universe.short(below_all).n | 5754 |
| rule_dense_universe.short(below_all).win | 0.327 |
| rule_dense_universe.short(below_all).PF | 1.029 |
| rule_dense_universe.short(below_all).mean_bps | 1.9 |
| rule_dense_universe.traded_total.n | 11228 |
| rule_dense_universe.traded_total.win | 0.301 |
| rule_dense_universe.traded_total.PF | 0.901 |
| rule_dense_universe.traded_total.mean_bps | -6.9 |
| rule_dense_universe.skipped_middle | 4779 |

## `base_rate_dense_full.json`

| 指标 | 值 |
|---|---|
| tag | base_rate_dense_full |
| window | <2026-05-04 (holdout untouched) |
| n_symbols | 225 |
| exit | TP5/SL2/72bar, maker cost 0.0006 |
| dense.n | 16262 |
| dense.win_rate | 0.2953 |
| dense.profit_factor | 0.874 |
| dense.mean_net | -0.00085 |
| dense.total_net | -13.833 |
| random_baseline.n | 16335 |
| random_baseline.win_rate | 0.2983 |
| random_baseline.profit_factor | 0.864 |
| random_baseline.mean_net | -0.00135 |
| random_baseline.total_net | -22.0873 |

## `chain_failure_attribution.json`

| 指标 | 值 |
|---|---|
| tag | chain_failure_attribution |
| discipline | train_only_open_time_lt_2026-05-04 |
| n_symbols | 233 |
| time_min | 2025-06-05 15:45:00+00:00 |
| time_max | 2026-05-03 23:15:00+00:00 |
| n_owner_short_boxes_train | 1361 |
| A_entry_vs_exit.lift.exit_lift_on_spread_entry | 0.17 |
| A_entry_vs_exit.lift.entry_lift_on_no_tp_exit | 0.13 |
| A_entry_vs_exit.lift.baseline_spread_tp5sl2_pf | 1.245 |
| A_entry_vs_exit.lift.spread_no_tp_pf | 1.415 |
| A_entry_vs_exit.lift.fixed_short_no_tp_pf | 1.285 |
| C_feature_filter.all_spread_short_no_tp.n | 6166 |
| C_feature_filter.all_spread_short_no_tp.win_rate | 0.2071 |
| C_feature_filter.all_spread_short_no_tp.mean_gross | 0.00414 |

## `consistency_e21_labels_vs_old_best.json`

| 指标 | 值 |
|---|---|
| dataset | datasets/dense_15m_full |
| split | val |
| iou_thr | 0.5 |
| n_images | 1255 |
| n_gt_boxes | 1297 |
| n_pred_boxes | 1495 |
| matched_iou50 | 643 |
| match_rate_vs_gt | 0.4958 |
| precision_like | 0.4301 |

## `consistency_e21_vs_new_best.json`

| 指标 | 值 |
|---|---|
| dataset | datasets/dense_15m_full |
| split | val |
| iou_thr | 0.5 |
| n_images | 1255 |
| n_gt_boxes | 1297 |
| n_pred_boxes | 1641 |
| matched_iou50 | 654 |
| match_rate_vs_gt | 0.5042 |
| precision_like | 0.3985 |

## `data_audit_summary.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-09 19:03 UTC |
| series_total | 1049 |
| by_bar.15m | 612 |
| by_bar.1H | 54 |
| by_bar.30m | 54 |
| by_bar.5m | 329 |
| flagged | 603 |
| structural_flagged | 299 |
| blacklist_candidate_n | 200 |
| okx_swap15_n | 363 |
| okx_swap15_stale | 1 |
| thresholds.max_gaps | 5 |
| thresholds.max_zero_vol | 0.02 |
| thresholds.max_spikes_flag | 3 |

## `deep_history_test.json`

| 指标 | 值 |
|---|---|
| n_series | 51 |
| cutoff | 2025-06-01 00:00:00+00:00 |

## `diag_barrier_grid.json`

> **判读**:近期块最优 = 持仓72根 / 止损无ATR / 止盈无ATR,近期净 +0.0452% vs 现行 5:2/72 的 +0.0067%,最差单笔 -37.5%

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| recent_from | 2026-02-20 |
| baseline.horizon | 72 |
| baseline.sl | 2 |
| baseline.tp | 5 |
| baseline.n | 5802 |
| baseline.net | 0.000312 |
| baseline.pf | 1.031 |
| baseline.worst | -0.2056 |
| baseline.recent_n | 1456 |
| baseline.recent_net | 6.7e-05 |
| baseline.recent_pf | 1.007 |

## `diag_box_width_rule.json`

| 指标 | 值 |
|---|---|
| n | 244 |
| owner_width_p50 | 11 |
| best | fixed_10 |

## `diag_cv_density_vs_owner.json`

> **判读**:CV 有效:放量 > 1.5x 精度 29.2% 区间下沿 19.9% > 基础率 18.2%

| 指标 | 值 |
|---|---|
| n | 390 |
| base_rate | 0.1821 |
| cv_keep_p50 | 0.185 |
| cv_drop_p50 | 0.1862 |
| mannwhitney_p | 0.351121 |

## `diag_detect_lag_compare.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-21T10:40:37.657099+00:00 |
| weights | /opt/fable-trading/models/owner_best.pt |
| conf | 0.3 |
| tip_conf | 0.22 |
| summary_by_mode.live.n_total | 32 |
| summary_by_mode.live.n_tip_fire | 1 |
| summary_by_mode.live.n_lag_le_30 | 1 |
| summary_by_mode.live.n_hit | 1 |
| summary_by_mode.live.n_miss | 31 |
| summary_by_mode.live.lag_min_median | 15 |
| summary_by_mode.live.lag_min_min | 15 |
| summary_by_mode.tip.n_total | 32 |
| summary_by_mode.tip.n_tip_fire | 1 |
| summary_by_mode.tip.n_lag_le_30 | 1 |

## `diag_detect_lag_eden.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-19T15:34:03.035993+00:00 |
| weights | /opt/fable-trading/models/owner_best.pt |
| conf | 0.3 |
| n_tip_fire | 0 |
| n_total | 2 |

## `diag_detect_lag_tip40.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-21T10:40:37.657935+00:00 |
| weights | /opt/fable-trading/models/owner_best.pt |
| conf | 0.3 |
| tip_conf | 0.22 |
| summary_by_mode.tip.n_total | 32 |
| summary_by_mode.tip.n_tip_fire | 1 |
| summary_by_mode.tip.n_lag_le_30 | 1 |
| summary_by_mode.tip.n_hit | 1 |
| summary_by_mode.tip.n_miss | 31 |
| summary_by_mode.tip.lag_min_median | 0 |
| summary_by_mode.tip.lag_min_min | 0 |
| n_tip_fire | 1 |
| n_total | 32 |

## `diag_edge_decay_attribution.json`

> **判读**:REGIME:环境变动幅度与胜率变动同量级 → 边可能随条件回来,应当对条件设门而不是关掉链路

| 指标 | 值 |
|---|---|
| conf_hi | 0.5 |
| breakeven | 0.2857 |
| win_ratio | 1.796 |
| env_max_ratio | 1.529 |

## `diag_ffd_vs_owner.json`

> **判读**:最好条件 放量>1.5x 精度 29.0% 下沿 19.6%

| 指标 | 值 |
|---|---|
| n | 384 |
| base_rate | 0.1797 |
| d_median | 0.15 |
| d_corr_median | 0.961898 |

## `diag_full_mode_lookahead.json`

| 指标 | 值 |
|---|---|
| window | 200 |
| stride | 50 |
| n_boxes | 143 |
| bar_in_win.min | 187 |
| bar_in_win.p10 | 199 |
| bar_in_win.p50 | 199 |
| bar_in_win.p90 | 199 |
| bar_in_win.max | 199 |
| lookahead_bars.min | 0 |
| lookahead_bars.p10 | 0 |
| lookahead_bars.p50 | 0 |
| lookahead_bars.p90 | 0 |
| lookahead_bars.max | 12 |
| lookahead_bars.mean | 0.1 |

## `diag_gold_cmp_gold_cmp_owner_short_star_v7s.json`

| 指标 | 值 |
|---|---|
| n | 206 |
| fired | 93 |
| iou_p50 | 0.2614 |
| iou_ge_50pct | 0.1613 |
| bar_off_right_p50 | 0 |
| width_ratio_p50 | 2 |
| owner_width_p50 | 10 |
| model_width_p50 | 20 |
| diagnosis | 主要是尺寸问题:v6 的框是 owner 的 2.00 倍 |

## `diag_gold_cmp_gold_cmp_owner_short_star_v8.json`

| 指标 | 值 |
|---|---|
| n | 206 |
| fired | 29 |
| iou_p50 | 0.5442 |
| iou_ge_50pct | 0.5862 |
| bar_off_right_p50 | 0 |
| width_ratio_p50 | 1.222 |
| owner_width_p50 | 9 |
| model_width_p50 | 11 |
| diagnosis | 位置和大小都接近,差距不大 |

## `diag_gold_cmp_gold_cmp_owner_short_star_v9.json`

| 指标 | 值 |
|---|---|
| n | 206 |
| fired | 173 |
| iou_p50 | 0.5962 |
| iou_ge_50pct | 0.7168 |
| bar_off_right_p50 | 0 |
| width_ratio_p50 | 1 |
| owner_width_p50 | 11 |
| model_width_p50 | 10 |
| diagnosis | 位置和大小都接近,差距不大 |

## `diag_holdout_power.json`

> **判读**:n=1739 时,单样本只能分辨 ≥39.4bp 的效应,配对能分辨 ≥33.7bp。近期块里要证的是:裸池净 +4.5bp(不够)、去障碍的增量 +3.8bp(不够)。 两个都不够 → 现在做 holdout 是白消耗一次,要么等样本量到 131,950 笔,要么先找更大的效应(例如把判断层修好再验)。

| 指标 | 值 |
|---|---|
| alpha | 0.01 |
| power | 0.8 |
| sigma.barrier | 0.0253105 |
| sigma.hold | 0.0480237 |
| sigma.paired_diff | 0.0411167 |
| effects_recent.hold_net | 0.000451801 |
| effects_recent.hold_minus_barrier | 0.000384575 |
| effects_pooled.hold_net | 0.00264513 |
| effects_pooled.hold_minus_barrier | 0.00233304 |

## `diag_judgment_under_new_exit.json`

> **判读**:判断层顶档在两种出场下都不优于全池(-32.91bp / -27.19bp)→ 该模型对本池没有选择力

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| n | 5802 |
| pool_net.barrier | 0.000312 |
| pool_net.hold | 0.002645 |
| top_lift_bp.barrier | -32.91 |
| top_lift_bp.hold | -27.19 |

## `diag_latency_budget.json`

| 指标 | 值 |
|---|---|
| scan_timing.n_symbols | 12 |
| scan_timing.n_windows | 36 |
| scan_timing.load_s | 0.5 |
| scan_timing.render_s | 0.46 |
| scan_timing.predict_s | 3.17 |
| scan_timing.total_s | 4.14 |
| scan_timing.per_symbol_s | 0.345 |
| universe | 220 |
| pulse_min | 15 |
| gate_min | 30 |
| budget_min.bar_close | 15 |
| budget_min.pulse_wait | 7.5 |
| budget_min.scan_full | 1.3 |
| budget_min.explained | 23.8 |

## `diag_maker_entry_fill.json`

> **判读**:最佳 = 入场挂 0bp 等 1 根 + 止盈挂单:成交率 100.0%,净/候选 -0.0179%,比现行 +5.09bp

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| n_candidates | 5802 |
| maker | 0.0006 |
| taker | 0.001 |
| baseline_all_taker.n_filled | 5802 |
| baseline_all_taker.fill_rate | 1 |
| baseline_all_taker.net_per_candidate | -0.000688 |
| baseline_all_taker.net_per_filled | -0.000688 |
| tp_maker_only.n_filled | 5802 |
| tp_maker_only.fill_rate | 1 |
| tp_maker_only.net_per_candidate | -0.000579 |
| tp_maker_only.net_per_filled | -0.000579 |

## `diag_mfe_giveback.json`

| 指标 | 值 |
|---|---|
| n | 5802 |
| mfe_p50 | 2.15 |
| giveback_p50 | 2.495 |
| sl_with_mfe_ge2 | 0.2631 |
| exits.base.gross_mean | 0.00131 |
| exits.base.gross_pf | 1.141 |
| exits.base.net_taker | 0.00031 |
| exits.trail.gross_mean | -0.0009 |
| exits.trail.gross_pf | 0.847 |
| exits.trail.net_taker | -0.0019 |
| exits.maflip.gross_mean | 0.00046 |
| exits.maflip.gross_pf | 1.073 |
| exits.maflip.net_taker | -0.00054 |

## `diag_no_barrier_tail_risk.json`

> **判读**:无障碍均值 +0.2645% 是现行的 8.5 倍,代价是最差单笔 -37.5%(现行 -20.6%)、1.72% 的单子亏超 10%。止损买的是尾部,不是收益。

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| n | 5802 |
| horizon | 72 |

## `diag_no_barrier_time_stability.json`

> **判读**:纯持在 3/4 四分位胜出,1 段落败 → 大体稳定但非一致,落败段需单独看

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| pooled.n | 5802 |
| pooled.from | 2025-06-07 |
| pooled.to | 2026-05-03 |
| pooled.barrier_net | 0.000312 |
| pooled.barrier_pf | 1.031 |
| pooled.hold_net | 0.002645 |
| pooled.hold_pf | 1.179 |

## `diag_retip_anchor_density.json`

> **判读**:重新锚定救不了:多数 owner 框附近根本没有机械意义上的密集 → 问题不在锚点,而在阈值/密集定义与 owner 的眼不一致(见分位表)

| 指标 | 值 |
|---|---|
| n | 1361 |
| skips.no_series | 0 |
| skips.oob | 0 |
| skips.holdout | 0 |
| skips.bad_ind | 0 |
| thresholds.FAST_MAX | 0.0028 |
| thresholds.FULL_MAX | 0.0055 |
| dense_at_cut_baseline | 0.014 |
| by_window.8.anchor_dense | 0.3255 |
| by_window.8.any_dense_upper_bound | 0.3468 |
| by_window.8.median_shift_bars | 7 |
| by_window.16.anchor_dense | 0.3652 |
| by_window.16.any_dense_upper_bound | 0.4034 |
| by_window.16.median_shift_bars | 8 |

## `diag_three_entry_exit_variants.json`

> **判读**:胜过基线的方案: 实体质量 >=50%(+0.061%)

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| n_candidates | 5802 |
| body_min | 0.5 |
| retest_wait | 12 |

## `diag_tip_edge_placement.json`

| 指标 | 值 |
|---|---|
| n_val_images | 1106 |
| n_fired | 338 |
| n_no_fire | 768 |
| conf | 0.3 |
| tip_edge_bars | 2 |
| gt_offset_from_tip.p50 | 0 |
| gt_offset_from_tip.max | 0 |
| pred_offset_from_tip.p50 | 0 |
| pred_offset_from_tip.p90 | 0 |
| pred_offset_from_tip.mean | 0 |
| pred_offset_from_tip.max | 0 |
| buckets_bars_short_of_tip.0 | 338 |
| buckets_bars_short_of_tip.1 | 0 |
| buckets_bars_short_of_tip.2 | 0 |

## `diag_tip_smoke.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-21T10:39:06.363763+00:00 |
| weights | /opt/fable-trading/models/owner_best.pt |
| conf | 0.3 |
| tip_conf | 0.22 |
| tip_smoke.tip.mode | tip |
| tip_smoke.tip.conf | 0.3 |
| tip_smoke.tip.tip_conf | 0.22 |
| tip_smoke.tip.n_symbols | 27 |
| tip_smoke.tip.n_fired | 0 |
| tip_smoke.live.mode | live |
| tip_smoke.live.conf | 0.3 |
| tip_smoke.live.n_symbols | 27 |
| tip_smoke.live.n_fired | 0 |

## `diag_tip_smoke_owner_side_short_tip_v1b.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-24T06:42:24.189506+00:00 |
| conf | 0.3 |
| tip_smoke.tip.mode | tip |
| tip_smoke.tip.conf | 0.3 |
| tip_smoke.tip.n_symbols | 27 |
| tip_smoke.tip.n_fired | 19 |
| tip_smoke.live.mode | live |
| tip_smoke.live.conf | 0.3 |
| tip_smoke.live.n_symbols | 27 |
| tip_smoke.live.n_fired | 4 |

## `diag_tip_smoke_v13.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-22T11:32:19.924995+00:00 |
| weights | models/owner_v13_pad200.pt |
| conf | 0.3 |
| tip_smoke.tip.mode | tip |
| tip_smoke.tip.conf | 0.3 |
| tip_smoke.tip.n_symbols | 27 |
| tip_smoke.tip.n_fired | 0 |
| tip_smoke.live.mode | live |
| tip_smoke.live.conf | 0.3 |
| tip_smoke.live.n_symbols | 27 |
| tip_smoke.live.n_fired | 0 |

## `diag_tip_smoke_v14.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-22T13:31:34.038427+00:00 |
| weights | models/owner_v14_pad200.pt |
| conf | 0.3 |
| tip_smoke.tip.mode | tip |
| tip_smoke.tip.conf | 0.3 |
| tip_smoke.tip.n_symbols | 27 |
| tip_smoke.tip.n_fired | 0 |
| tip_smoke.live.mode | live |
| tip_smoke.live.conf | 0.3 |
| tip_smoke.live.n_symbols | 27 |
| tip_smoke.live.n_fired | 0 |

## `diag_tip_smoke_v15.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-22T16:21:46.440716+00:00 |
| weights | models/owner_v15_tipval.pt |
| conf | 0.3 |
| tip_smoke.tip.mode | tip |
| tip_smoke.tip.conf | 0.3 |
| tip_smoke.tip.n_symbols | 27 |
| tip_smoke.tip.n_fired | 0 |
| tip_smoke.live.mode | live |
| tip_smoke.live.conf | 0.3 |
| tip_smoke.live.n_symbols | 27 |
| tip_smoke.live.n_fired | 0 |

## `diag_v6_conf_sweep.json`

> **判读**:高置信度有区分:conf>=0.5 胜率 37.8%

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6.csv |
| weights | best.pt |
| breakeven_win | 0.2857 |

## `diag_v6_conf_sweep_judgment_yolo_short_v6_wide.json`

> **判读**:高置信度有区分:conf>=0.5 胜率 34.4%

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| weights | best.pt |
| breakeven_win | 0.2857 |

## `diag_v6_vs_gold_boxes.json`

| 指标 | 值 |
|---|---|
| n | 206 |
| fired | 106 |
| iou_p50 | 0.5319 |
| iou_ge_50pct | 0.5849 |
| bar_off_right_p50 | 0 |
| width_ratio_p50 | 1.236 |
| owner_width_p50 | 10 |
| model_width_p50 | 14 |
| diagnosis | 位置和大小都接近,差距不大 |

## `diag_v8_recall_vs_conf.json`

> **判读**:NARROW:门槛降到 0.01 召回仍只有 16.3%(v6 在 0.05 时 53.1%) → 模型真的学窄了,训练宽度需要带分布

| 指标 | 值 |
|---|---|
| n_tips | 147 |
| recall_by_conf.v6.0.01 | 0.8435 |
| recall_by_conf.v6.0.03 | 0.6054 |
| recall_by_conf.v6.0.05 | 0.5306 |
| recall_by_conf.v6.0.1 | 0.4014 |
| recall_by_conf.v6.0.2 | 0.2721 |
| recall_by_conf.v6.0.3 | 0.1633 |
| recall_by_conf.v6.0.4 | 0.0476 |
| recall_by_conf.v6.0.5 | 0 |
| recall_by_conf.v8.0.01 | 0.1633 |
| recall_by_conf.v8.0.03 | 0.1565 |
| recall_by_conf.v8.0.05 | 0.1497 |
| recall_by_conf.v8.0.1 | 0.1497 |
| recall_by_conf.v8.0.2 | 0.1293 |

## `diag_v9_live_lag.json`

> **判读**:v9 端到端 39.1 分钟,仍超 30 分钟门 → 换 v9 后 live 能产出可交易信号

| 指标 | 值 |
|---|---|
| gate_min | 30 |
| pulse_min | 15 |
| universe | 220 |
| models.v11 (产生542分钟那个).n_fired | 2 |
| models.v11 (产生542分钟那个).n_symbols | 40 |
| models.v11 (产生542分钟那个).box_age_min | 15 |
| models.v11 (产生542分钟那个).scan_full_min | 1.5 |
| models.v11 (产生542分钟那个).end_to_end_min | 39 |
| models.v9 (今天训的).n_fired | 10 |
| models.v9 (今天训的).n_symbols | 40 |
| models.v9 (今天训的).box_age_min | 15 |
| models.v9 (今天训的).scan_full_min | 1.56 |
| models.v9 (今天训的).end_to_end_min | 39.1 |

## `diag_v9_prefilter_recall.json`

> **判读**:没有预筛能保住 ≥95% 候选 —— 必须全扫,否则候选池被静默改变

| 指标 | 值 |
|---|---|
| n_symbols | 2 |
| n_bars | 1400 |
| n_fires | 114 |
| wall_min | 8 |
| prefilters.v16_dense.keep_frac | 0.1614 |
| prefilters.v16_dense.recall | 0.3333 |
| prefilters.v16_dense.n_kept_fires | 38 |
| prefilters.v9_dense.keep_frac | 0.3886 |
| prefilters.v9_dense.recall | 0.6228 |
| prefilters.v9_dense.n_kept_fires | 71 |
| prefilters.break.keep_frac | 0.1829 |
| prefilters.break.recall | 0.5702 |
| prefilters.break.n_kept_fires | 65 |
| prefilters.break_loose.keep_frac | 0.3264 |

## `diag_vol_scaled_barriers.json`

> **判读**:净最高:对照: 无障碍纯持72根,+0.2645% vs 基线 +0.0312%;但 TP率从 27.3% 塌到 0.0%,超时率 100.0% —— 障碍基本不再触发,这是换了策略而非改进出场,须与「无障碍纯持72根」对照读

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6_wide.csv |
| n | 5802 |
| ewm_span | 32 |
| low_atr_cut | 0.00499114 |

## `direction_classifier.json`

| 指标 | 值 |
|---|---|
| exit | TP3/SL1 |
| signals | 15897 |

## `direction_select_base_rate.json`

> **判读**:择向未救出可交易边

| 指标 | 值 |
|---|---|
| tag | direction_select_base_rate |
| success_criterion.pf_maker_ge | 1.3 |
| success_criterion.pass_label | 值得谈影子/继续 |
| success_criterion.fail_label | 择向未救出可交易边 |
| best_side.variant | spread_expand_chg8 |
| best_side.side | short_only |
| best_side.pf_maker | 1.245 |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry | next_bar_open |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.order_score_min | 3 |

## `direction_select_smoke.json`

> **判读**:值得谈影子/继续

| 指标 | 值 |
|---|---|
| tag | direction_select_smoke |
| success_criterion.pf_maker_ge | 1.3 |
| success_criterion.pass_label | 值得谈影子/继续 |
| success_criterion.fail_label | 择向未救出可交易边 |
| best_side.variant | spread_expand_chg8 |
| best_side.side | short_only |
| best_side.pf_maker | 1.382 |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry | next_bar_open |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.order_score_min | 3 |

## `directional_test.json`

| 指标 | 值 |
|---|---|
| exit | TP3xATR/SL1xATR |
| n_signals | 15899 |
| LONG.n | 15899 |
| LONG.win | 0.238 |
| LONG.PF | 0.774 |
| LONG.mean_bps | -8.9 |
| SHORT.n | 15899 |
| SHORT.win | 0.276 |
| SHORT.PF | 0.982 |
| SHORT.mean_bps | -0.7 |
| BREAKOUT_confirmed.n | 6365 |
| BREAKOUT_confirmed.win | 0.272 |
| BREAKOUT_confirmed.PF | 0.928 |
| BREAKOUT_confirmed.mean_bps | -2.6 |

## `e3_margin_diagnosis.json`

| 指标 | 值 |
|---|---|
| diagnosis.recall_iou50_core | 0.5042 |
| diagnosis.recall_iou50_boundary | 0 |
| diagnosis.fn_gap_pp | 50.4 |
| diagnosis.n_core | 1297 |
| diagnosis.n_boundary | 0 |
| diagnosis.usability_iou30.recall | 0.6399 |
| diagnosis.usability_iou30.precision | 0.5058 |
| e3_build.kept_train | 5805 |
| e3_build.dropped_all_boundary | 0 |
| e3_build.dropped_near_miss_bg | 0 |

## `e3_sparse_and_two_stage.json`

| 指标 | 值 |
|---|---|
| tag | e3_sparse_and_two_stage |
| n_symbols | 233 |
| n_owner_short_train | 1361 |
| n_owner_cuts_matched | 1284 |
| n_tips_raw | 19250 |
| time_min | 2025-06-05 15:45:00+00:00 |
| time_max | 2026-05-03 23:15:00+00:00 |
| elapsed_sec | 40.2 |
| predeclared.e3_n_aim | 1500 |
| predeclared.base_thr | 0.00383 |
| predeclared.base_gap | 18 |
| predeclared.costs.maker | 0.0006 |
| predeclared.costs.legacy | 0.002 |
| e3.pick.panel | E3_count |

## `e3_sparse_smoke.json`

| 指标 | 值 |
|---|---|
| tag | e3_sparse_smoke |
| n_symbols | 15 |
| n_owner_short_train | 1361 |
| n_owner_cuts_matched | 76 |
| n_tips_raw | 1225 |
| time_min | 2025-06-07 23:15:00+00:00 |
| time_max | 2026-05-03 14:15:00+00:00 |
| elapsed_sec | 2.5 |
| predeclared.e3_n_aim | 1500 |
| predeclared.base_thr | 0.00383 |
| predeclared.base_gap | 18 |
| predeclared.costs.maker | 0.0006 |
| predeclared.costs.legacy | 0.002 |
| e3.pick.panel | E3_count |

## `entry_align_and_regime.json`

| 指标 | 值 |
|---|---|
| tag | entry_align_and_regime |
| n_symbols | 233 |
| n_fit_symbols_with_owner | 212 |
| time_range.min | 2025-06-05 15:45:00+00:00 |
| time_range.max | 2026-05-03 23:15:00+00:00 |
| n_owner_short_train | 1361 |
| fit.n_pos | 1284 |
| fit.n_neg | 5136 |
| fit.auc_disclosure_only | 0.9725 |
| E1_verdict.baseline_overlap_w18.owner_recall | 0.2523 |
| E1_verdict.baseline_overlap_w18.jaccard | 0.0452 |
| E1_verdict.success_line_overlap.owner_recall>= | 0.45 |
| E1_verdict.success_line_overlap.or_jaccard>= | 0.12 |
| E1_verdict.success_line_pf | 1.3 |

## `entry_align_smoke.json`

| 指标 | 值 |
|---|---|
| tag | entry_align_smoke |
| n_symbols | 15 |
| n_fit_symbols_with_owner | 15 |
| time_range.min | 2025-06-07 23:15:00+00:00 |
| time_range.max | 2026-05-03 14:15:00+00:00 |
| n_owner_short_train | 1361 |
| fit.n_pos | 76 |
| fit.n_neg | 304 |
| fit.auc_disclosure_only | 0.9612 |
| E1_verdict.baseline_overlap_w18.owner_recall | 0.1842 |
| E1_verdict.baseline_overlap_w18.jaccard | 0.0292 |
| E1_verdict.success_line_overlap.owner_recall>= | 0.45 |
| E1_verdict.success_line_overlap.or_jaccard>= | 0.12 |
| E1_verdict.success_line_pf | 1.3 |

## `entry_edge_multi_exit.json`

| 指标 | 值 |
|---|---|
| owner_boxes.n | 4979 |
| owner_boxes.ret4_mean_bps | -1.3 |
| owner_boxes.ret8_mean_bps | -2.6 |
| owner_boxes.ret12_mean_bps | -11.6 |
| owner_boxes.ret24_mean_bps | -31.7 |
| owner_boxes.ret48_mean_bps | -39.8 |
| owner_boxes.mfe_mean | 0.0377 |
| owner_boxes.mae_mean | -0.0447 |
| owner_boxes.net_tp1_sl1.win | 0.475 |
| owner_boxes.net_tp1_sl1.PF | 0.797 |
| owner_boxes.net_tp1_sl1.mean_bps | -8.5 |
| owner_boxes.net_tp1_sl1.5.win | 0.546 |
| owner_boxes.net_tp1_sl1.5.PF | 0.718 |
| owner_boxes.net_tp1_sl1.5.mean_bps | -15 |

## `entry_timing_close_vs_next.json`

> **判读**:入场约定未救出可交易边（两档皆 <1.3）

| 指标 | 值 |
|---|---|
| tag | entry_timing_close_vs_next |
| success_criterion.pf_maker_ge | 1.3 |
| success_criterion.single_variable | entry fill only; TP/SL not swept |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| data.n_symbols | 236 |
| data.arrange_tips_raw | 19250 |
| data.arrange_skips_raw | 8306 |
| best_by_entry.next_open.variant | spread_expand_chg8 |
| best_by_entry.next_open.side | short_only |
| best_by_entry.next_open.pf_maker | 1.245 |

## `entry_timing_smoke.json`

> **判读**:入场约定改变结论（至少一档过 1.3）

| 指标 | 值 |
|---|---|
| tag | entry_timing_smoke |
| success_criterion.pf_maker_ge | 1.3 |
| success_criterion.single_variable | entry fill only; TP/SL not swept |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| data.n_symbols | 20 |
| data.arrange_tips_raw | 1501 |
| data.arrange_skips_raw | 658 |
| best_by_entry.next_open.variant | spread_expand_chg8 |
| best_by_entry.next_open.side | short_only |
| best_by_entry.next_open.pf_maker | 1.382 |

## `eval_short_tip_v2_vs_owner_gold.json`

> **判读**:v3 过于保守:真检出也丢了 → 不能只看误检下降

| 指标 | 值 |
|---|---|
| conf | 0.3 |
| tip_edge_bars | 2 |

## `frozen_eval_comparison.json`

| 指标 | 值 |
|---|---|
| v3_coco.f1 | 0.558 |
| v3_coco.p | 0.541 |
| v3_coco.r | 0.576 |
| v4.f1 | 0.563 |
| v4.p | 0.498 |
| v4.r | 0.646 |
| v5_coco.f1 | 0.641 |
| v5_coco.p | 0.567 |
| v5_coco.r | 0.738 |
| v5_from_v4.f1 | 0.663 |
| v5_from_v4.p | 0.758 |
| v5_from_v4.r | 0.59 |
| v6_chain.conf | 0.15 |
| v6_chain.f1 | 0.595 |

## `golden_round1.json`

| 指标 | 值 |
|---|---|
| summary.tasks | 80 |
| summary.rule_boxes | 100 |
| summary.owner_boxes | 44 |
| summary.accepted | 30 |
| summary.reshaped | 0 |
| summary.deleted_rule_fp | 70 |
| summary.added_rule_fn | 14 |
| summary.accept_rate | 0.3 |
| summary.delete_rate | 0.7 |
| summary.reshape_stats.n | 0 |
| summary.images_with_changes | 46 |

## `golden_round2.json`

| 指标 | 值 |
|---|---|
| self_consistency_20_repeats.iou50.f1 | 0.88 |
| self_consistency_20_repeats.iou50.r1_boxes | 11 |
| self_consistency_20_repeats.iou50.r2_boxes | 14 |
| self_consistency_20_repeats.iou50.matched | 11 |
| self_consistency_20_repeats.iou30.f1 | 0.88 |
| self_consistency_20_repeats.iou30.r1_boxes | 11 |
| self_consistency_20_repeats.iou30.r2_boxes | 14 |
| self_consistency_20_repeats.iou30.matched | 11 |
| round2_fresh_vs_rules.images | 188 |
| round2_fresh_vs_rules.rule_boxes | 217 |
| round2_fresh_vs_rules.kept | 58 |
| round2_fresh_vs_rules.deleted | 159 |
| round2_fresh_vs_rules.added | 29 |
| round2_fresh_vs_rules.owner_boxes | 87 |

## `h11_tiered.json`

| 指标 | 值 |
|---|---|
| median_notional_24h | 1.2129e+06 |

## `h13_btc_regime.json`

| 指标 | 值 |
|---|---|
| n_pool | 23683 |
| n_usable | 23683 |
| n_symbols | 256 |

## `h5_vol_adaptive.json`

| 指标 | 值 |
|---|---|
| q33 | 0.0037386 |
| q66 | 0.00499341 |

## `h9_spot_trend_filter.json`

| 指标 | 值 |
|---|---|
| threshold_q90 | 0.39735 |
| horizon_bars | 72 |
| maker_cost | 0.0016 |
| flag_coverage | 1 |
| top_bucket.no_filter.n | 160 |
| top_bucket.no_filter.mean_net_maker | 0.00152 |
| top_bucket.no_filter.win_rate | 0.4813 |
| top_bucket.up_slope_only.n | 80 |
| top_bucket.up_slope_only.mean_net_maker | 0.00162 |
| top_bucket.up_slope_only.win_rate | 0.5 |
| top_bucket.above_ma_only.n | 82 |
| top_bucket.above_ma_only.mean_net_maker | 0.00203 |
| top_bucket.above_ma_only.win_rate | 0.5122 |
| top_bucket.both.n | 61 |

## `h9_swap_feature_retrain.json`

| 指标 | 值 |
|---|---|
| dataset | data/swap_replication/swap_tp5_sl2.csv |
| horizon_bars | 72 |
| maker_cost | 0.0006 |
| h9_feature | h1_above_ma |
| feature_coverage | 1 |
| feature_pass_rate | 0.3411 |
| baseline.name | baseline |
| baseline.best_iteration | 25 |
| baseline.val_auc | 0.5601 |
| baseline.perm_p | 0.001 |
| baseline.top_gross | 0.00086 |
| baseline.top_net_maker | 0.00026 |
| baseline.top_win_rate | 0.3245 |
| h9_feature_model.name | h9_feature_model |

## `h9_swap_trend_filter.json`

| 指标 | 值 |
|---|---|
| dataset | data/swap_replication/swap_tp5_sl2.csv |
| threshold_q90 | 0.38741 |
| horizon_bars | 72 |
| maker_cost | 0.0006 |
| flag_coverage | 1 |
| top_bucket.no_filter.n | 151 |
| top_bucket.no_filter.mean_net_maker | 0.00026 |
| top_bucket.no_filter.win_rate | 0.3576 |
| top_bucket.up_slope_only.n | 52 |
| top_bucket.up_slope_only.mean_net_maker | 0.00073 |
| top_bucket.up_slope_only.win_rate | 0.4038 |
| top_bucket.above_ma_only.n | 58 |
| top_bucket.above_ma_only.mean_net_maker | 0.00066 |
| top_bucket.above_ma_only.win_rate | 0.3966 |

## `h9_trend_filter.json`

| 指标 | 值 |
|---|---|
| threshold_q90 | 0.39735 |
| flag_coverage | 1 |
| top_bucket.no_filter.n | 160 |
| top_bucket.no_filter.mean_net_maker | 0.00152 |
| top_bucket.no_filter.win_rate | 0.4813 |
| top_bucket.up_slope_only.n | 80 |
| top_bucket.up_slope_only.mean_net_maker | 0.00162 |
| top_bucket.up_slope_only.win_rate | 0.5 |
| top_bucket.above_ma_only.n | 82 |
| top_bucket.above_ma_only.mean_net_maker | 0.00203 |
| top_bucket.above_ma_only.win_rate | 0.5122 |
| top_bucket.both.n | 61 |
| top_bucket.both.mean_net_maker | 0.00155 |
| top_bucket.both.win_rate | 0.5082 |

## `h_a_vol_regime_gate.json`

| 指标 | 值 |
|---|---|
| n_total | 2125 |
| n_hivol | 669 |
| n_lovol | 1456 |

## `holdout9_midvol.json`

> **判读**:❌ 未通过:区间下沿 16.8% <= 盈亏平衡 28.6%。按预注册,该配置作废,不得换边界重试。

| 指标 | 值 |
|---|---|
| prereg | analysis/p_prereg_holdout9_midvol.md |
| consumption | 9 |
| conf_hi | 0.5 |
| n_candidates | 1739 |
| n_high_conf | 46 |
| n_in_band | 10 |
| wins | 4 |
| win_rate | 0.4 |
| breakeven | 0.2857 |

## `hts_dataset_build.json`

| 指标 | 值 |
|---|---|
| hypothesis | H-TS |
| cutoff_exclusive_utc | 2026-05-04 00:00:00+00:00 |
| window | 200 |
| stats.train_kept | 5743 |
| stats.train_post_cutoff | 715 |
| stats.train_unresolved | 213 |
| stats.val_kept | 2076 |
| stats.val_unresolved | 164 |
| stats.val_post_cutoff | 253 |
| n_kept | 7819 |
| n_dropped | 1345 |
| drop_reasons.post_cutoff | 968 |
| drop_reasons.unresolved | 377 |

## `hts_experiment_summary.json`

| 指标 | 值 |
|---|---|
| run | owner_hts_chain |
| hypothesis | H-TS |
| cutoff | 2026-05-04 exclusive (window end) |
| dataset.hypothesis | H-TS |
| dataset.cutoff_exclusive_utc | 2026-05-04 00:00:00+00:00 |
| dataset.window | 200 |
| dataset.stats.train_kept | 5743 |
| dataset.stats.train_post_cutoff | 715 |
| dataset.stats.train_unresolved | 213 |
| dataset.stats.val_kept | 2076 |
| dataset.stats.val_unresolved | 164 |
| dataset.stats.val_post_cutoff | 253 |
| dataset.n_kept | 7819 |
| dataset.n_dropped | 1345 |

## `it08_rolling_retrain.json`

| 指标 | 值 |
|---|---|
| exit | TP5/SL2 |

## `it09_both_sides.json`

| 指标 | 值 |
|---|---|
| exit | TP5/SL2 |
| LONG_side.n | 1829 |
| SHORT_side.n | 2125 |

## `it10_regime_side_select.json`

| 指标 | 值 |
|---|---|
| exit | TP5/SL2 |
| long_cands | 1829 |
| short_cands | 2125 |

## `it11_adaptive_side.json`

| 指标 | 值 |
|---|---|
| K_days | 21 |

## `it12_breakout_straddle.json`

| 指标 | 值 |
|---|---|
| cost | taker 0.10% RT |

## `it13_fade_straddle.json`

| 指标 | 值 |
|---|---|
| cost | maker 0.06% RT |

## `it14_visual_direction_precheck.json`

| 指标 | 值 |
|---|---|
| n | 4012 |
| up_rate | 0.482 |

## `it15_tip_remap.json`

| 指标 | 值 |
|---|---|
| n_by_def.A_cut | 2504 |
| n_by_def.B_trough | 2498 |
| n_by_def.C_last_dense | 2501 |
| results.A_cut.offset_from_cut_median | 0 |
| results.B_trough.offset_from_cut_median | 10 |
| results.C_last_dense.offset_from_cut_median | 8 |
| holdout | FORBIDDEN |

## `it17_short_rule_vs_lgbm.json`

| 指标 | 值 |
|---|---|
| n | 25602 |
| cost_primary | 0.002 |
| raw_gross_mean | 0.00269 |
| tp_rate | 0.284 |
| tp5sl2_breakeven | 0.286 |
| rho_sign_flips | 0 |
| results.atr_pct_HIGH.mean | 0.00542 |
| results.atr_pct_HIGH.min | 0.00071 |
| results.atr_pct_HIGH.n_pos | 5 |
| results.atr_pct_HIGH.n_folds | 5 |
| results.atr_pct_LOW.mean | 0.00023 |
| results.atr_pct_LOW.min | -0.00253 |
| results.atr_pct_LOW.n_pos | 3 |
| results.atr_pct_LOW.n_folds | 5 |

## `it18_atr_edge_mechanism.json`

> **判读**:ANTI-SELECTION(高波动其实是更差的交易): 毛PF与胜率双双下降;净值变好只因 TP/SL 按 ATR 等比放大而成本固定 = 放大赌注,不是选股能力

| 指标 | 值 |
|---|---|
| pool_n | 25602 |
| barriers | TP5xATR/SL2xATR |

## `it19_short_at_real_execution_cost.json`

| 指标 | 值 |
|---|---|
| pool_n | 25602 |
| gross_mean | 0.00269 |
| gross_PF | 1.323 |
| walkforward_atr_high_topdecile.SWAP_MAKER 0.06% (executor 拿不到).cost | 0.0006 |
| walkforward_atr_high_topdecile.SWAP_MAKER 0.06% (executor 拿不到).mean | 0.00682 |
| walkforward_atr_high_topdecile.SWAP_MAKER 0.06% (executor 拿不到).n_pos | 5 |
| walkforward_atr_high_topdecile.SWAP_MAKER 0.06% (executor 拿不到).n_folds | 5 |
| walkforward_atr_high_topdecile.SWAP_TAKER 0.10% (纯手续费,无滑点).cost | 0.001 |
| walkforward_atr_high_topdecile.SWAP_TAKER 0.10% (纯手续费,无滑点).mean | 0.00642 |
| walkforward_atr_high_topdecile.SWAP_TAKER 0.10% (纯手续费,无滑点).n_pos | 5 |
| walkforward_atr_high_topdecile.SWAP_TAKER 0.10% (纯手续费,无滑点).n_folds | 5 |
| walkforward_atr_high_topdecile.taker+0.05% 滑点 = 0.15%.cost | 0.0015 |
| walkforward_atr_high_topdecile.taker+0.05% 滑点 = 0.15%.mean | 0.00592 |
| walkforward_atr_high_topdecile.taker+0.05% 滑点 = 0.15%.n_pos | 5 |

## `judgment_v6_cpcv.json`

> **判读**:判断层有效:10/15 切分胜过裸池,中位净 +0.00276

| 指标 | 值 |
|---|---|
| pool | judgment_yolo_short_v6.csv |
| n | 1748 |
| n_splits | 15 |
| n_paths | 5 |
| purged | 180 |
| embargoed | 328 |
| beats_raw | 10/15 |

## `judgment_v6_short_walkforward.json`

| 指标 | 值 |
|---|---|
| pool | data/judgment_yolo_short_v6.csv |
| n | 1748 |
| top_frac | 0.2 |

## `launch_entry_base_rate.json`

| 指标 | 值 |
|---|---|
| tag | launch_entry_base_rate |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry | next_bar_open |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.max_wait_bars_after_dense | 48 |
| discipline.range_n | 20 |
| discipline.vol_m | 20 |
| discipline.vol_k | 1.5 |
| discipline.spread_chg8_thr | 0.00383 |
| data.n_symbols | 236 |
| data.triggers.emergence_always_long | 16145 |

## `launch_entry_long_short.json`

| 指标 | 值 |
|---|---|
| tag | launch_entry_long_short |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry | next_bar_open |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.max_wait_bars_after_dense | 48 |
| discipline.range_n | 20 |
| discipline.vol_m | 20 |
| discipline.vol_k | 1.5 |
| discipline.spread_chg8_thr | 0.00383 |
| data.n_symbols | 236 |
| data.triggers_both.emergence_always_long | 16145 |

## `launch_entry_long_short_smoke.json`

| 指标 | 值 |
|---|---|
| tag | launch_entry_long_short_smoke |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry | next_bar_open |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.max_wait_bars_after_dense | 48 |
| discipline.range_n | 20 |
| discipline.vol_m | 20 |
| discipline.vol_k | 1.5 |
| discipline.spread_chg8_thr | 0.00383 |
| data.n_symbols | 8 |
| data.triggers_both.emergence_always_long | 543 |

## `launch_entry_smoke.json`

| 指标 | 值 |
|---|---|
| tag | launch_entry_smoke |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry | next_bar_open |
| discipline.exit | TP5/SL2/72bar |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.max_wait_bars_after_dense | 48 |
| discipline.range_n | 20 |
| discipline.vol_m | 20 |
| discipline.vol_k | 1.5 |
| discipline.spread_chg8_thr | 0.00383 |
| data.n_symbols | 8 |
| data.triggers.emergence_always_long | 543 |

## `low_tf_backtest.json`

| 指标 | 值 |
|---|---|
| horizon_policy | wall-clock match 15m*72 ≈ 18h |
| costs.base_portfolio | 0.003 |
| costs.top_decile_rt | 0.002 |
| max_concurrent | 10 |

## `ml_opt_rules_expanded_sweep.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_dataset_v2_expanded.csv |
| tag | ml_opt_rules_expanded |
| cost_round_trip | 0.002 |
| splits.train_n | 6367 |
| splits.val_n | 1598 |
| splits.holdout_n | 2214 |
| splits.pos_rate_train | 0.3405 |
| splits.pos_rate_val | 0.4262 |
| baseline_top_net | 0.00101 |
| best_variant | reg_realized_ret |
| best_top_net | 0.00306 |
| delta_vs_baseline | 0.00205 |

## `ml_opt_swap_tp5_sweep.json`

| 指标 | 值 |
|---|---|
| dataset | data/swap_replication/swap_tp5_sl2.csv |
| tag | ml_opt_swap_tp5 |
| cost_round_trip | 0.002 |
| splits.train_n | 6027 |
| splits.val_n | 1510 |
| splits.holdout_n | 1709 |
| splits.pos_rate_train | 0.29 |
| splits.pos_rate_val | 0.3212 |
| baseline_top_net | -0.00114 |
| best_variant | reg_realized_ret |
| best_top_net | 0.00304 |
| delta_vs_baseline | 0.00418 |

## `ml_opt_yolo_sweep.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap.csv |
| tag | ml_opt_yolo |
| cost_round_trip | 0.002 |
| splits.train_n | 1382 |
| splits.val_n | 349 |
| splits.holdout_n | 640 |
| splits.pos_rate_train | 0.3075 |
| splits.pos_rate_val | 0.4212 |
| baseline_top_net | 0.02441 |
| best_variant | reg_realized_ret |
| best_top_net | 0.02923 |
| delta_vs_baseline | 0.00482 |

## `mtf_sweep.json`

| 指标 | 值 |
|---|---|
| costs.maker | 0.0006 |
| costs.taker | 0.001 |
| baseline_15m_val_n | 1510 |

## `owner_base_comparison.json`

| 指标 | 值 |
|---|---|
| owner_base_yolo11s_yaml.conf | 0.15 |
| owner_base_yolo11s_yaml.f1 | 0.282 |
| owner_base_yolo11s_yaml.p | 0.37 |
| owner_base_yolo11s_yaml.r | 0.227 |
| owner_base_yolo11s_pt.conf | 0.2 |
| owner_base_yolo11s_pt.f1 | 0.39 |
| owner_base_yolo11s_pt.p | 0.455 |
| owner_base_yolo11s_pt.r | 0.341 |
| owner_base_best_pt.conf | 0.2 |
| owner_base_best_pt.f1 | 0.35 |
| owner_base_best_pt.p | 0.389 |
| owner_base_best_pt.r | 0.318 |

## `owner_box_alpha.json`

| 指标 | 值 |
|---|---|
| owner_boxes | 4979 |
| random_negs | 9859 |
| R_owner_direct_SURVIVORSHIP_INFLATED.n | 4979 |
| R_owner_direct_SURVIVORSHIP_INFLATED.win | 0.334 |
| R_owner_direct_SURVIVORSHIP_INFLATED.PF | 1.062 |
| R_owner_direct_SURVIVORSHIP_INFLATED.mean_net | 0.00062 |
| random_baseline.n | 9859 |
| random_baseline.win | 0.3018 |
| random_baseline.PF | 0.914 |
| random_baseline.mean_net | -0.00086 |
| single_split.AUC_market_only | 0.8759 |
| single_split.owner_like_random_top10.n | 339 |
| single_split.owner_like_random_top10.win | 0.3599 |
| single_split.owner_like_random_top10.PF | 1.077 |

## `owner_box_alpha_broad.json`

| 指标 | 值 |
|---|---|
| owner_boxes | 4979 |
| random_negs | 9859 |
| R_owner_direct_SURVIVORSHIP_INFLATED.n | 4979 |
| R_owner_direct_SURVIVORSHIP_INFLATED.win | 0.334 |
| R_owner_direct_SURVIVORSHIP_INFLATED.PF | 1.062 |
| R_owner_direct_SURVIVORSHIP_INFLATED.mean_net | 0.00062 |
| random_baseline.n | 9859 |
| random_baseline.win | 0.3018 |
| random_baseline.PF | 0.914 |
| random_baseline.mean_net | -0.00086 |
| single_split.AUC_market_only | 0.872 |
| single_split.owner_like_random_top10.n | 339 |
| single_split.owner_like_random_top10.win | 0.3333 |
| single_split.owner_like_random_top10.PF | 1.137 |

## `owner_box_dir_tp3sl1.json`

| 指标 | 值 |
|---|---|
| owner_boxes | 4979 |
| random_negs | 9859 |
| R_owner_direct_SURVIVORSHIP_INFLATED.n | 4979 |
| R_owner_direct_SURVIVORSHIP_INFLATED.win | 0.2788 |
| R_owner_direct_SURVIVORSHIP_INFLATED.PF | 1.032 |
| R_owner_direct_SURVIVORSHIP_INFLATED.mean_net | 0.00019 |
| random_baseline.n | 9859 |
| random_baseline.win | 0.2444 |
| random_baseline.PF | 0.858 |
| random_baseline.mean_net | -0.00083 |
| single_split.AUC_market_only | 0.872 |
| single_split.owner_like_random_top10.n | 339 |
| single_split.owner_like_random_top10.win | 0.2655 |
| single_split.owner_like_random_top10.PF | 0.965 |

## `owner_detector_v1.json`

| 指标 | 值 |
|---|---|
| conf | 0.2 |
| f1 | 0.35 |
| p | 0.389 |
| r | 0.318 |
| tp | 14 |
| fp | 22 |
| fn | 30 |

## `owner_detector_v2.json`

| 指标 | 值 |
|---|---|
| best.conf | 0.2 |
| best.f1 | 0.368 |
| best.p | 0.468 |
| best.r | 0.303 |
| best.tp | 37 |
| best.fp | 42 |
| best.fn | 85 |

## `owner_detector_v3.json`

| 指标 | 值 |
|---|---|
| owner_v3_coco.conf | 0.2 |
| owner_v3_coco.f1 | 0.457 |
| owner_v3_coco.p | 0.483 |
| owner_v3_coco.r | 0.433 |
| owner_v3_coco.tp | 84 |
| owner_v3_coco.fp | 90 |
| owner_v3_coco.fn | 110 |
| owner_v3_chain.conf | 0.15 |
| owner_v3_chain.f1 | 0.438 |
| owner_v3_chain.p | 0.39 |
| owner_v3_chain.r | 0.5 |
| owner_v3_chain.tp | 97 |
| owner_v3_chain.fp | 152 |
| owner_v3_chain.fn | 97 |

## `owner_detector_v4.json`

| 指标 | 值 |
|---|---|
| conf | 0.2 |
| f1 | 0.511 |
| p | 0.497 |
| r | 0.525 |

## `owner_detector_v5.json`

| 指标 | 值 |
|---|---|
| owner_v5_coco.conf | 0.2 |
| owner_v5_coco.f1 | 0.495 |
| owner_v5_coco.p | 0.472 |
| owner_v5_coco.r | 0.52 |
| owner_v5_coco.tp | 247 |
| owner_v5_coco.fp | 276 |
| owner_v5_coco.fn | 228 |
| owner_v5_from_v4.conf | 0.2 |
| owner_v5_from_v4.f1 | 0.493 |
| owner_v5_from_v4.p | 0.496 |
| owner_v5_from_v4.r | 0.491 |
| owner_v5_from_v4.tp | 233 |
| owner_v5_from_v4.fp | 237 |
| owner_v5_from_v4.fn | 242 |

## `owner_label_feature_verdict.json`

| 指标 | 值 |
|---|---|
| tag | owner_label_feature_verdict |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.costs_reported.swap_maker | 0.0006 |
| discipline.costs_reported.legacy_p0 | 0.002 |
| discipline.primary_compare_cost | swap_maker_vs_emergence_0.874 |
| data.cuts_extracted | 3318 |
| data.pos_feature_rows | 3318 |
| data.random_neg | 9729 |
| data.hard_neg | 3241 |
| data.symbols_labeled | 233 |
| data.skip_stats.empty | 6180 |
| data.skip_stats.mad_fail | 1870 |
| data.skip_stats.cut_oob | 16 |
| data.skip_stats.no_series | 139 |

## `owner_label_smoke.json`

| 指标 | 值 |
|---|---|
| tag | owner_label_smoke |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.costs_reported.swap_maker | 0.0006 |
| discipline.costs_reported.legacy_p0 | 0.002 |
| discipline.primary_compare_cost | swap_maker_vs_emergence_0.874 |
| data.cuts_extracted | 200 |
| data.pos_feature_rows | 200 |
| data.random_neg | 600 |
| data.hard_neg | 0 |
| data.symbols_labeled | 24 |
| data.skip_stats.empty | 387 |
| data.skip_stats.mad_fail | 191 |
| lgbm_disclosure.val_auc | 1 |
| causal_rule.logic | AND |

## `owner_side_feature_verdict.json`

| 指标 | 值 |
|---|---|
| tag | owner_side_feature_verdict |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.long_settlement | label_candidate |
| discipline.short_settlement | label_short_candidate |
| discipline.success_line | per-side causal-rule PF@maker >= 1.3 |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| label_counts.long | 1152 |
| label_counts.short | 1361 |
| label_counts.skip | 12 |
| label_counts.empty | 0 |
| by_side.long.side | long |
| by_side.long.n_labeled_boxes | 1152 |
| by_side.long.pos_feature_rows | 1152 |

## `owner_side_rich_features_verdict.json`

| 指标 | 值 |
|---|---|
| tag | owner_side_rich_features_verdict |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.long_settlement | label_candidate |
| discipline.short_settlement | label_short_candidate |
| discipline.success_line | per-side causal-rule PF@maker >= 1.3 |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| label_counts.long | 1152 |
| label_counts.short | 1361 |
| label_counts.skip | 12 |
| label_counts.empty | 0 |
| n_features | 116 |
| narrow_reference.long_pf_maker | 0.917 |
| narrow_reference.short_pf_maker | 1.127 |

## `owner_v12_htip_frozen.json`

| 指标 | 值 |
|---|---|
| conf | 0.3 |
| f1 | 0.65 |
| p | 0.615 |
| r | 0.69 |
| tp | 158 |
| fp | 99 |
| fn | 71 |

## `owner_v2_plan.json`

| 指标 | 值 |
|---|---|
| dataset | datasets/dense_owner_v2 |
| counts.missing | 0 |
| pool_images | 768 |
| pool_boxes | 423 |

## `p0_summary.json`

| 指标 | 值 |
|---|---|
| merged_long_n_pos | 301 |
| merged_long_n_neg | 1365 |
| fav_pos_median | 0.0178499 |
| fav_neg_median | 0.0173109 |
| fav_U_p | 0.844015 |
| fav_rank_biserial | -0.00723839 |
| adv_pos_median | -0.00387553 |
| adv_neg_median | -0.0119343 |
| adv_U_p | 9.23294e-14 |
| adv_rank_biserial | 0.274029 |
| pos_median_net | 0.0139744 |
| roundtrip_cost | 0.002 |

## `p2a_e21_val_metrics.json`

| 指标 | 值 |
|---|---|
| mAP50 | 0.8503 |
| mAP50_95 | 0.6655 |
| precision | 0.8106 |
| recall | 0.7047 |

## `p2a_val_metrics.json`

| 指标 | 值 |
|---|---|
| mAP50 | 0.2428 |
| mAP50-95 | 0.0933 |
| precision | 0.3389 |
| recall | 0.4817 |

## `p2a_val_metrics_smoke3.json`

| 指标 | 值 |
|---|---|
| mAP50 | 0.8353 |
| mAP50-95 | 0.5929 |
| precision | 0.7623 |
| recall | 0.7099 |

## `p2b_ma206_comparison.json`

| 指标 | 值 |
|---|---|
| label | TP5/SL2 h72 |
| universe | OKX USDT_SWAP 15m |
| v206_series_scanned | 116 |

## `p2b_metrics.json`

| 指标 | 值 |
|---|---|
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 1068 |
| splits.val.n | 270 |
| splits.holdout.n | 564 |
| best_iteration | 16 |
| val.n | 270 |
| val.positive_rate | 0.4519 |
| val.roc_auc | 0.5653 |
| val.pr_auc | 0.5029 |
| val.top_decile.n | 27 |
| val.top_decile.mean_realized_ret | 0.0013 |
| val.top_decile.mean_net_ret | -0.0007 |
| val.top_decile.win_rate | 0.5185 |
| val.all_mean_net_ret | -0.00115 |

## `p2b_v2_expanded_final_metrics.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_dataset_v2_expanded.csv |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 6367 |
| splits.val.n | 1598 |
| splits.holdout.n | 2214 |
| best_iteration | 19 |
| val.n | 1598 |
| val.positive_rate | 0.4262 |
| val.roc_auc | 0.5647 |
| val.pr_auc | 0.4715 |
| val.top_decile.n | 159 |
| val.top_decile.mean_realized_ret | 0.00301 |
| val.top_decile.mean_net_ret | 0.00101 |
| val.top_decile.win_rate | 0.5094 |

## `p2b_v2_expanded_metrics.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_dataset_v2_expanded.csv |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 6367 |
| splits.val.n | 1598 |
| splits.holdout.n | 2214 |
| best_iteration | 19 |
| val.n | 1598 |
| val.positive_rate | 0.4262 |
| val.roc_auc | 0.5647 |
| val.pr_auc | 0.4715 |
| val.top_decile.n | 159 |
| val.top_decile.mean_realized_ret | 0.00301 |
| val.top_decile.mean_net_ret | 0.00101 |
| val.top_decile.win_rate | 0.5094 |

## `p2b_v2_expanded_short_metrics.json`

| 指标 | 值 |
|---|---|
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 23477 |
| splits.val.n | 5896 |
| splits.holdout.n | 9349 |
| best_iteration | 40 |
| val.n | 5896 |
| val.positive_rate | 0.3468 |
| val.roc_auc | 0.5986 |
| val.pr_auc | 0.4275 |
| val.top_decile.n | 589 |
| val.top_decile.mean_realized_ret | 0.00263 |

## `p2b_v2_strict_metrics.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_dataset_v2_strict.csv |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 1829 |
| splits.val.n | 464 |
| splits.holdout.n | 550 |
| best_iteration | 22 |
| val.n | 464 |
| val.positive_rate | 0.4526 |
| val.roc_auc | 0.5428 |
| val.pr_auc | 0.4893 |
| val.top_decile.n | 46 |
| val.top_decile.mean_realized_ret | 0.00291 |
| val.top_decile.mean_net_ret | 0.00091 |
| val.top_decile.win_rate | 0.5435 |

## `p2b_v2_strict_short_metrics.json`

| 指标 | 值 |
|---|---|
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 5714 |
| splits.val.n | 1438 |
| splits.holdout.n | 2423 |
| best_iteration | 12 |
| val.n | 1438 |
| val.positive_rate | 0.2886 |
| val.roc_auc | 0.5331 |
| val.pr_auc | 0.3121 |
| val.top_decile.n | 143 |
| val.top_decile.mean_realized_ret | -6e-05 |

## `p2b_yolo_owner_side_short_5_6m_feat_mirror_metrics.json`

| 指标 | 值 |
|---|---|
| side | short |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 983 |
| splits.val.n | 248 |
| splits.holdout.n | 0 |
| best_iteration | 11 |
| val.n | 248 |
| val.positive_rate | 0.2581 |
| val.roc_auc | 0.5897 |
| val.pr_auc | 0.3068 |
| val.top_decile.n | 24 |

## `p2b_yolo_owner_side_short_5_6m_metrics.json`

| 指标 | 值 |
|---|---|
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 983 |
| splits.val.n | 248 |
| splits.holdout.n | 0 |
| best_iteration | 5 |
| val.n | 248 |
| val.positive_rate | 0.2581 |
| val.roc_auc | 0.5993 |
| val.pr_auc | 0.3485 |
| val.top_decile.n | 24 |
| val.top_decile.mean_realized_ret | 0.00262 |

## `p2b_yolo_owner_side_short_tip_v1b_5_6m_metrics.json`

| 指标 | 值 |
|---|---|
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 983 |
| splits.val.n | 248 |
| splits.holdout.n | 0 |
| best_iteration | 5 |
| val.n | 248 |
| val.positive_rate | 0.2581 |
| val.roc_auc | 0.5993 |
| val.pr_auc | 0.3485 |
| val.top_decile.n | 24 |
| val.top_decile.mean_realized_ret | 0.00262 |

## `p2b_yolo_short_100_6m_reg_metrics.json`

| 指标 | 值 |
|---|---|
| side | short |
| objective | regression |
| score_semantics | predicted_realized_ret |
| n_features | 28 |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 20255 |
| splits.val.n | 5107 |
| splits.holdout.n | 0 |
| best_iteration | 11 |
| val.n | 5107 |
| val.positive_rate | 0.2651 |

## `p2b_yolo_short_100_6m_reg_walkforward.json`

| 指标 | 值 |
|---|---|
| objective | regression |
| side | short |
| n_folds | 5 |
| cost | 0.002 |
| holdout_rows_excluded | 0 |
| dev_n | 25532 |
| baseline_single_split_top_decile_net | 0.00471 |
| rho_mean | -0.0103 |
| rho_min | -0.0831 |
| net_mean | 0.00305 |
| net_min | -9e-05 |

## `p2b_yolo_short_30_6m_mirror_metrics.json`

| 指标 | 值 |
|---|---|
| side | short |
| objective | binary |
| score_semantics | class_probability |
| n_features | 28 |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 5973 |
| splits.val.n | 1500 |
| splits.holdout.n | 0 |
| best_iteration | 1 |
| val.n | 1500 |
| val.positive_rate | 0.266 |

## `p2b_yolo_short_30_6m_mirror_topk10_metrics.json`

| 指标 | 值 |
|---|---|
| side | short |
| objective | binary |
| score_semantics | class_probability |
| n_features | 10 |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 5973 |
| splits.val.n | 1500 |
| splits.holdout.n | 0 |
| best_iteration | 1 |
| val.n | 1500 |
| val.positive_rate | 0.266 |

## `p2b_yolo_short_30_6m_reg_metrics.json`

| 指标 | 值 |
|---|---|
| side | short |
| objective | regression |
| score_semantics | predicted_realized_ret |
| n_features | 28 |
| bar | 15m |
| horizon_bars | 72 |
| purge_window | 0 days 18:15:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| splits.train.n | 5973 |
| splits.val.n | 1500 |
| splits.holdout.n | 0 |
| best_iteration | 14 |
| val.n | 1500 |
| val.positive_rate | 0.266 |

## `p2b_yolo_short_30_6m_reg_walkforward.json`

| 指标 | 值 |
|---|---|
| objective | regression |
| side | short |
| n_folds | 5 |
| cost | 0.002 |
| holdout_rows_excluded | 0 |
| dev_n | 7498 |
| baseline_single_split_top_decile_net | 0.00371 |
| rho_mean | 0.0277 |
| rho_min | -0.0213 |
| net_mean | 0.00336 |
| net_min | -0.00513 |

## `p3_backtest.json`

| 指标 | 值 |
|---|---|
| score_threshold_val_q90 | 0.42438 |
| n_candidates | 10255 |
| n_eligible | 864 |
| config.max_concurrent | 10 |
| config.base_cost_round_trip | 0.003 |
| config.accept_window_start | 2026-05-04 00:00:00+00:00 |
| cost_sweep_accept_window.0.002.n_trades | 157 |
| cost_sweep_accept_window.0.002.net_total_units | 0.1631 |
| cost_sweep_accept_window.0.002.net_return_on_capital | 0.0163 |
| cost_sweep_accept_window.0.002.mean_net_per_trade | 0.00104 |
| cost_sweep_accept_window.0.002.win_rate | 0.5032 |
| cost_sweep_accept_window.0.002.profit_factor | 1.296 |
| cost_sweep_accept_window.0.002.max_drawdown_pct | 0.0177 |
| cost_sweep_accept_window.0.003.n_trades | 157 |

## `p3_maker_val_sim.json`

| 指标 | 值 |
|---|---|
| threshold_val_q90 | 0.39735 |
| maker.n_trades | 124 |
| maker.net_total_units | 0.13 |
| maker.net_return_on_capital | 0.013 |
| maker.mean_net_per_trade | 0.00105 |
| maker.win_rate | 0.4435 |
| maker.profit_factor | 1.271 |
| maker.max_drawdown_pct | 0.007 |
| maker.outcome_counts.sl | 64 |
| maker.outcome_counts.tp | 50 |
| maker.outcome_counts.timeout | 10 |
| maker.fill_rate | 0.815 |
| taker.n_trades | 152 |
| taker.net_total_units | 0.0204 |

## `p3_ml_opt_backtest_compare.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap_v11.csv |
| generated_at | 2026-07-20T14:04:59.141688+00:00 |
| active | v11_reg |
| shadow | v8_reg |
| detector_mainline | owner_v12_htip (owner_best.pt) |
| detector_previous | owner_v11_chain (owner_best_pre_v12.pt) |
| variants.v11_reg.variant | ACTIVE · v11 池回归 |
| variants.v11_reg.objective | regression |
| variants.v11_reg.dataset | data/judgment_yolo_swap_v11.csv |
| variants.v11_reg.score_threshold_val_q90 | 0.02022 |
| variants.v11_reg.n_candidates | 26653 |
| variants.v11_reg.n_eligible | 3681 |
| variants.v8_reg.variant | SHADOW · v8 池回归（历史） |
| variants.v8_reg.objective | regression |

## `p3_ml_opt_backtest_compare_pre_v11_table.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap_v8.csv |
| generated_at | 2026-07-16T14:30:53.420731+00:00 |
| active | v8_reg |
| shadow | binary_yolo |
| variants.v8_reg.variant | ACTIVE · v8 池回归 |
| variants.v8_reg.objective | regression |
| variants.v8_reg.dataset | data/judgment_yolo_swap_v8.csv |
| variants.v8_reg.score_threshold_val_q90 | 0.02171 |
| variants.v8_reg.n_candidates | 17573 |
| variants.v8_reg.n_eligible | 2281 |
| variants.binary_yolo.variant | SHADOW · 旧池二分类 |
| variants.binary_yolo.objective | binary |
| variants.binary_yolo.dataset | data/judgment_yolo_swap.csv |
| variants.binary_yolo.score_threshold_val_q90 | 0.710867 |

## `p3_spot_h9_maker_val_sim.json`

| 指标 | 值 |
|---|---|
| horizon_bars | 72 |
| threshold_val_q90 | 0.39735 |
| costs.maker | 0.0016 |
| costs.taker | 0.003 |
| flag_coverage | 1 |
| pass_rate.h1_above_ma | 0.3586 |
| pass_rate.h1_up_slope | 0.2766 |
| maker.n_trades | 124 |
| maker.net_total_units | 0.13 |
| maker.net_return_on_capital | 0.013 |
| maker.mean_net_per_trade | 0.00105 |
| maker.win_rate | 0.4435 |
| maker.profit_factor | 1.271 |
| maker.max_drawdown_pct | 0.007 |

## `p3_swap_h9_maker_val_sim.json`

| 指标 | 值 |
|---|---|
| dataset | data/swap_replication/swap_tp5_sl2.csv |
| horizon_bars | 72 |
| threshold_val_q90 | 0.38741 |
| costs.maker | 0.0006 |
| costs.taker | 0.001 |
| flag_coverage | 1 |
| pass_rate.h1_above_ma | 0.3411 |
| pass_rate.h1_up_slope | 0.2788 |
| maker.n_trades | 123 |
| maker.net_total_units | -0.0187 |
| maker.net_return_on_capital | -0.0019 |
| maker.mean_net_per_trade | -0.00015 |
| maker.win_rate | 0.3333 |
| maker.profit_factor | 0.964 |

## `p3_yolo_backtest.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap.csv |
| score_threshold_val_q90 | 0.71087 |
| n_candidates | 2385 |
| n_eligible | 272 |
| config.max_concurrent | 10 |
| config.base_cost_round_trip | 0.003 |
| config.accept_window_start | 2026-05-04 00:00:00+00:00 |
| cost_sweep_accept_window.0.002.n_trades | 49 |
| cost_sweep_accept_window.0.002.net_total_units | 1.0768 |
| cost_sweep_accept_window.0.002.net_return_on_capital | 0.1077 |
| cost_sweep_accept_window.0.002.mean_net_per_trade | 0.02198 |
| cost_sweep_accept_window.0.002.win_rate | 0.8163 |
| cost_sweep_accept_window.0.002.profit_factor | 8.665 |
| cost_sweep_accept_window.0.002.max_drawdown_pct | 0.0042 |

## `p3_yolo_maker_val_sim.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap.csv |
| horizon_bars | 72 |
| threshold_val_q90 | 0.71087 |
| costs.maker | 0.0006 |
| costs.taker | 0.001 |
| flag_coverage | 1 |
| pass_rate.h1_above_ma | 0.4986 |
| pass_rate.h1_up_slope | 0.4241 |
| maker.n_trades | 35 |
| maker.net_total_units | 0.8998 |
| maker.net_return_on_capital | 0.09 |
| maker.mean_net_per_trade | 0.02571 |
| maker.win_rate | 0.8857 |
| maker.profit_factor | 41.42 |

## `p3_yolo_reg_backtest.json`

| 指标 | 值 |
|---|---|
| objective | regression |
| score_threshold_val_q90 | 0.0165354 |
| n_candidates | 2385 |
| n_eligible | 381 |
| config.max_concurrent | 10 |
| config.base_cost_round_trip | 0.003 |
| config.accept_window_start | 2026-05-04 00:00:00+00:00 |
| config.active_config | tp5_sl2_swap_yolo_reg |
| cost_sweep_accept_window.0.002.n_trades | 102 |
| cost_sweep_accept_window.0.002.net_total_units | 3.3372 |
| cost_sweep_accept_window.0.002.net_return_on_capital | 0.3337 |
| cost_sweep_accept_window.0.002.mean_net_per_trade | 0.03272 |
| cost_sweep_accept_window.0.002.win_rate | 0.7941 |
| cost_sweep_accept_window.0.002.profit_factor | 7.284 |

## `p3_yolo_v10_reg_backtest.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap_v10.csv |
| score_threshold_val_q90 | 0.7544 |
| n_candidates | 23480 |
| n_eligible | 2235 |
| config.max_concurrent | 10 |
| config.base_cost_round_trip | 0.003 |
| config.accept_window_start | 2026-05-04 00:00:00+00:00 |
| cost_sweep_accept_window.0.002.n_trades | 414 |
| cost_sweep_accept_window.0.002.net_total_units | 10.7145 |
| cost_sweep_accept_window.0.002.net_return_on_capital | 1.0715 |
| cost_sweep_accept_window.0.002.mean_net_per_trade | 0.02588 |
| cost_sweep_accept_window.0.002.win_rate | 0.8575 |
| cost_sweep_accept_window.0.002.profit_factor | 15.3 |
| cost_sweep_accept_window.0.002.max_drawdown_pct | 0.0036 |

## `p3_yolo_v11_reg_backtest.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap_v11.csv |
| score_threshold_val_q90 | 0.02022 |
| n_candidates | 26653 |
| n_eligible | 3681 |
| config.max_concurrent | 10 |
| config.base_cost_round_trip | 0.003 |
| config.accept_window_start | 2026-05-04 00:00:00+00:00 |
| cost_sweep_accept_window.0.002.n_trades | 703 |
| cost_sweep_accept_window.0.002.net_total_units | 25.2842 |
| cost_sweep_accept_window.0.002.net_return_on_capital | 2.5284 |
| cost_sweep_accept_window.0.002.mean_net_per_trade | 0.03597 |
| cost_sweep_accept_window.0.002.win_rate | 0.7738 |
| cost_sweep_accept_window.0.002.profit_factor | 6.985 |
| cost_sweep_accept_window.0.002.max_drawdown_pct | 0.0074 |

## `p3_yolo_v8_reg_backtest.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap_v8.csv |
| score_threshold_val_q90 | 0.02171 |
| n_candidates | 17573 |
| n_eligible | 2281 |
| config.max_concurrent | 10 |
| config.base_cost_round_trip | 0.003 |
| config.accept_window_start | 2026-05-04 00:00:00+00:00 |
| cost_sweep_accept_window.0.002.n_trades | 428 |
| cost_sweep_accept_window.0.002.net_total_units | 15.9134 |
| cost_sweep_accept_window.0.002.net_return_on_capital | 1.5913 |
| cost_sweep_accept_window.0.002.mean_net_per_trade | 0.03718 |
| cost_sweep_accept_window.0.002.win_rate | 0.7991 |
| cost_sweep_accept_window.0.002.profit_factor | 7.931 |
| cost_sweep_accept_window.0.002.max_drawdown_pct | 0.0068 |

## `p_execution_slippage.json`

| 指标 | 值 |
|---|---|
| forward.n_rows | 28 |
| forward.fresh_rows | 0 |
| forward.decision_trades | 0 |
| forward.hindsight_excluded | 22 |
| forward.lag_min_min | 77.6 |
| forward.lag_min_med | 542.3 |
| forward.lag_min_max | 2307.1 |
| forward.lag_min_mean | 735.671 |
| ledger.events.order_partial | 1 |
| ledger.events.order_failed | 5 |
| ledger.events.paused | 328 |
| ledger.fail_reasons.51008_margin | 5 |
| ledger.n_order_partial | 1 |
| ledger.n_clean_fill_with_price_diff | 0 |

## `p_tip_subset_backtest.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-21T05:05:34.254813+00:00 |
| meta.generated_at | 2026-07-20T17:10:55.302092+00:00 |
| meta.dataset | data/judgment_yolo_swap_v11.csv |
| meta.threshold_val_q90 | 0.0202214 |
| meta.val_start | 2026-03-12 06:45:00+00:00 |
| meta.holdout_start | 2026-05-04 00:00:00+00:00 |
| meta.n_eligible | 2904 |
| meta.n_eligible_val | 413 |
| eligible_counts.pre_holdout_total | 2904 |
| eligible_counts.val_window | 413 |
| eligible_counts.rerender_skipped | 0 |
| eligible_counts.tip_strict_total | 117 |
| eligible_counts.tip_strict_val | 14 |
| eligible_counts.tip_92_total | 128 |

## `p_v12_score_shift.json`

| 指标 | 值 |
|---|---|
| threshold_val_q90 | 0.0202214 |
| round_trip_cost | 0.002 |
| window_days | 29.35 |
| v11_baseline.n_candidates | 2400 |
| v11_baseline.n_symbols | 263 |
| v11_baseline.candidates_per_day | 81.76 |
| v11_baseline.pos_rate | 0.4279 |
| v11_baseline.score_quantiles.p10 | -0.01124 |
| v11_baseline.score_quantiles.p25 | -0.0086 |
| v11_baseline.score_quantiles.p50 | 0.01022 |
| v11_baseline.score_quantiles.p75 | 0.01449 |
| v11_baseline.score_quantiles.p90 | 0.02062 |
| v11_baseline.score_quantiles.p99 | 0.05098 |
| v11_baseline.score_mean | 0.00563 |

## `p_weight_centric_val.json`

| 指标 | 值 |
|---|---|
| dataset | data/judgment_yolo_swap_v11.csv |
| threshold_val_q90 | 0.02022 |
| tier_bounds_val.q90 | 0.02022 |
| tier_bounds_val.q95 | 0.02548 |
| tier_bounds_val.q99 | 0.04857 |
| calibration.p_min_at_threshold | 0.6326 |
| calibration.scale_mean_p_minus_pmin | 0.0697 |
| capital_units | 10 |

## `pad200_cut_audit_stats.json`

| 指标 | 值 |
|---|---|
| n_sample | 177 |

## `real_tip_fair_v12.json`

| 指标 | 值 |
|---|---|
| weights | models/owner_v16_tipuni_cold.pt |
| conf | 0.3 |
| tip_edge_bars | 2 |
| window | 200 |
| ma_protocol | full-MA |
| n_sheet | 47 |
| n_eval | 47 |
| n_skipped | 0 |
| gold_counts_eval.tip-empty-ok | 33 |
| gold_counts_eval.tip-miss-dense | 6 |
| gold_counts_eval.tip-hit | 3 |
| gold_counts_eval.tip-noise | 5 |
| should_fire.hit_raw.n | 9 |
| should_fire.hit_raw.k | 3 |

## `real_tip_fair_v14.json`

| 指标 | 值 |
|---|---|
| weights | models/owner_v14_pad200.pt |
| conf | 0.3 |
| tip_edge_bars | 2 |
| window | 200 |
| ma_protocol | full-MA |
| preview_dir | analysis/output/v13_real_tip_preview |
| n_sheet | 47 |
| n_eval | 47 |
| n_skipped | 0 |
| gold_counts_eval.tip-empty-ok | 33 |
| gold_counts_eval.tip-miss-dense | 6 |
| gold_counts_eval.tip-hit | 3 |
| gold_counts_eval.tip-noise | 5 |
| should_fire.hit_raw.n | 9 |

## `real_tip_fair_v15.json`

| 指标 | 值 |
|---|---|
| weights | models/owner_v16_tipuni_cold.pt |
| conf | 0.3 |
| tip_edge_bars | 2 |
| window | 200 |
| ma_protocol | full-MA |
| n_sheet | 47 |
| n_eval | 47 |
| n_skipped | 0 |
| gold_counts_eval.tip-empty-ok | 33 |
| gold_counts_eval.tip-miss-dense | 6 |
| gold_counts_eval.tip-hit | 3 |
| gold_counts_eval.tip-noise | 5 |
| should_fire.hit_raw.n | 9 |
| should_fire.hit_raw.k | 3 |

## `regime_adaptive_two_layer.json`

| 指标 | 值 |
|---|---|
| exit | TP5/SL2 |
| n | 2432 |
| sides.long | 1277 |
| sides.short | 1155 |
| sides.skip | 1582 |

## `rule_fit_golden.json`

| 指标 | 值 |
|---|---|
| baseline_current_rules.fit_f1 | 0.4037 |
| baseline_current_rules.p | 0.282 |
| baseline_current_rules.r | 0.71 |
| grid_size | 324 |

## `samesource_v16_100.json`

| 指标 | 值 |
|---|---|
| tag | samesource_v16_100 |
| rows | 4014 |
| test_n | 1205 |
| raw_test_base_rate.n | 1205 |
| raw_test_base_rate.win_rate | 0.3178 |
| raw_test_base_rate.profit_factor | 0.963 |
| raw_test_base_rate.mean_net | -0.00022 |
| raw_test_base_rate.total_net | -0.2622 |
| score_top_slices.top10.n | 120 |
| score_top_slices.top10.win_rate | 0.2833 |
| score_top_slices.top10.profit_factor | 0.826 |
| score_top_slices.top10.mean_net | -0.00107 |
| score_top_slices.top10.total_net | -0.1285 |
| score_top_slices.top20.n | 241 |

## `samesource_v16_judgment.json`

| 指标 | 值 |
|---|---|
| tag | samesource_v16_judgment |
| rows | 1330 |
| test_n | 400 |
| raw_test_base_rate.n | 400 |
| raw_test_base_rate.win_rate | 0.3175 |
| raw_test_base_rate.profit_factor | 0.951 |
| raw_test_base_rate.mean_net | -0.00027 |
| raw_test_base_rate.total_net | -0.1068 |
| score_top_slices.top10.n | 40 |
| score_top_slices.top10.win_rate | 0.25 |
| score_top_slices.top10.profit_factor | 0.842 |
| score_top_slices.top10.mean_net | -0.00094 |
| score_top_slices.top10.total_net | -0.0377 |
| score_top_slices.top20.n | 80 |

## `short_quality_judgment.json`

| 指标 | 值 |
|---|---|
| exit | TP5/SL2 |
| candidates | 2125 |

## `short_replication.json`

| 指标 | 值 |
|---|---|
| config.name | swap_short_tp5_sl2 |
| config.tp | 5 |
| config.sl | 2 |
| n_candidates | 8976 |
| splits.train.n | 5737 |
| splits.train.positive_rate | 0.3214 |
| splits.val.n | 1445 |
| splits.val.positive_rate | 0.2789 |
| best_iteration | 58 |
| val.n | 1445 |
| val.positive_rate | 0.2789 |
| val.roc_auc | 0.6174 |
| val.pr_auc | 0.3599 |
| val.top_decile.n | 144 |

## `short_trend_ab.json`

| 指标 | 值 |
|---|---|
| tag | short_trend_ab |
| verdict_a | A 稳健过线（PF≥1.3 且非少数月份独撑） |
| verdict_b | B 手标 short+趋势出显著好于规则 |
| holdout_suggestion | 值得申请 holdout#7 对照一次（仅建议，本脚本不跑） |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry_rule | spread_expand_chg8 |
| discipline.entry_fill | next_open |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.success_pf_maker | 1.3 |
| discipline.n_owner_short_labels_train | 1361 |
| data.n_symbols | 233 |
| data.n_short_entry_fires | 6213 |

## `short_trend_holdout7.json`

> **判读**:证伪

| 指标 | 值 |
|---|---|
| tag | short_trend_holdout7 |
| holdout_consumption_n | 7 |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry_rule | spread_expand_chg8 |
| discipline.entry_fill | next_open |
| discipline.direction | short_only |
| discipline.costs.swap_maker | 0.0006 |
| discipline.costs.legacy_p0 | 0.002 |
| discipline.atr_pct_min | 0.0015 |
| discipline.success_pf_maker | 1.3 |
| data.n_symbols | 311 |
| data.n_short_entry_fires | 2345 |
| data.spread_chg8_thr | 0.00383 |
| a_short.no_tp_sl2_h144.exit_rule | SL2 only, no TP; timeout 144 |

## `swap_h1h9_stack.json`

| 指标 | 值 |
|---|---|
| tp5_sl2.n_pool | 9312 |
| tp5_sl2.threshold | 0.3874 |
| tp5_sl2.top_all.n | 151 |
| tp5_sl2.top_all.net_maker016 | -0.00074 |
| tp5_sl2.top_all.net_swap006 | 0.00026 |
| tp5_sl2.top_all.win | 0.3576 |
| tp5_sl2.top_above_ma.n | 58 |
| tp5_sl2.top_above_ma.net_maker016 | -0.00034 |
| tp5_sl2.top_above_ma.net_swap006 | 0.00066 |
| tp5_sl2.top_above_ma.win | 0.3966 |
| tp5_sl2.top_against.n | 93 |
| tp5_sl2.top_against.net_maker016 | -0.00099 |
| tp5_sl2.top_against.net_swap006 | 1e-05 |
| tp5_sl2.top_against.win | 0.3333 |

## `tip_mapping_owner_intent_audit.json`

> **判读**:MECHANICAL_TIP_GAP

| 指标 | 值 |
|---|---|
| holdout | FORBIDDEN |
| A_image_geometry.n_labeled | 2513 |
| A_image_geometry.box_right_frac.n | 2513 |
| A_image_geometry.box_right_frac.median | 0.5075 |
| A_image_geometry.box_right_frac.p25 | 0.2875 |
| A_image_geometry.box_right_frac.p75 | 0.7075 |
| A_image_geometry.box_right_frac.mean | 0.4973 |
| A_image_geometry.box_right_frac.frac_ge_0.9 | 0.0497 |
| A_image_geometry.box_right_frac.frac_ge_0.8 | 0.1313 |
| A_image_geometry.width_bars.n | 2513 |
| A_image_geometry.width_bars.median | 12 |
| A_image_geometry.width_bars.p25 | 9 |
| A_image_geometry.width_bars.p75 | 14 |
| A_image_geometry.width_bars.mean | 11.7521 |

## `tip_rate_v11_baseline.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_best.pt |
| conf | 0.3 |
| n | 111 |
| skipped | 9 |
| tip_hits | 1 |
| tip_hit_rate | 0.009 |
| generated_at | 2026-07-19T15:54:37.691679+00:00 |

## `tip_rate_v12.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| dataset | datasets/dense_owner_v11 |
| split | val |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 111 |
| tip_hit_rate | 0.925 |
| generated_at | 2026-07-20T11:28:05.634854+00:00 |

## `tip_rate_v12_fullma.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| ma_mode | full-MA |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_best.pt |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 2 |
| tip_hit_rate | 0.0167 |
| generated_at | 2026-07-22T16:49:52.352959+00:00 |

## `tip_rate_v13_pad200.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_v13_pad200.pt |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 1 |
| tip_hit_rate | 0.0083 |
| generated_at | 2026-07-22T11:32:18.621288+00:00 |

## `tip_rate_v14_fullma.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| ma_mode | full-MA |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_v14_pad200.pt |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 3 |
| tip_hit_rate | 0.025 |
| generated_at | 2026-07-22T16:50:12.545990+00:00 |

## `tip_rate_v14_pad200.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_v14_pad200.pt |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 4 |
| tip_hit_rate | 0.0333 |
| generated_at | 2026-07-22T13:31:32.736450+00:00 |

## `tip_rate_v15_fullma.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| ma_mode | full-MA |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_v15_tipval.pt |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 1 |
| tip_hit_rate | 0.0083 |
| generated_at | 2026-07-22T16:50:33.190906+00:00 |

## `tip_rate_v15_tipval.json`

| 指标 | 值 |
|---|---|
| method | true_tip_rerender |
| dataset | datasets/dense_owner_v11 |
| split | val |
| weights | models/owner_v15_tipval.pt |
| conf | 0.3 |
| n | 120 |
| skipped | 0 |
| tip_hits | 2 |
| tip_hit_rate | 0.0167 |
| generated_at | 2026-07-22T16:21:45.033726+00:00 |

## `tip_smoke_forced_windows.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-21T16:29:05.837061+00:00 |
| n_log_rows | 32 |
| n_symbols | 27 |

## `tip_subset_meta.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-20T17:10:55.302092+00:00 |
| dataset | data/judgment_yolo_swap_v11.csv |
| threshold_val_q90 | 0.0202214 |
| val_start | 2026-03-12 06:45:00+00:00 |
| holdout_start | 2026-05-04 00:00:00+00:00 |
| n_eligible | 2904 |
| n_eligible_val | 413 |

## `trend_direction_test.json`

| 指标 | 值 |
|---|---|
| exit | TP3/SL1 |
| n | 15899 |
| rules.ema200.n | 15899 |
| rules.ema200.win | 0.259 |
| rules.ema200.PF | 0.874 |
| rules.ema200.mean_bps | -4.8 |
| rules.ema120.n | 15899 |
| rules.ema120.win | 0.254 |
| rules.ema120.PF | 0.859 |
| rules.ema120.mean_bps | -5.4 |
| rules.slope55.n | 15899 |
| rules.slope55.win | 0.256 |
| rules.slope55.PF | 0.877 |
| rules.slope55.mean_bps | -4.7 |

## `trend_exit_base_rate.json`

> **判读**:趋势出场抬过 PF≥1.3

| 指标 | 值 |
|---|---|
| tag | trend_exit_base_rate |
| success_criterion.pf_maker_ge | 1.3 |
| best_by_sum_net.exit | no_tp_sl2_h144 |
| best_by_sum_net.side | short_only |
| best_by_sum_net.n | 6166 |
| best_by_sum_net.sum_net_maker | 21.8096 |
| best_by_sum_net.mean_net_maker | 0.00354 |
| best_by_sum_net.pf_maker | 1.415 |
| best_by_sum_net.win_rate | 0.2071 |
| best_by_sum_net.mean_hold_bars | 52.56 |
| best_by_sum_net.max_dd_sum_net_maker | -0.82559 |
| discipline.holdout_start | 2026-05-04 00:00:00+00:00 |
| discipline.entry_rule | spread_expand_chg8 |
| discipline.entry_fill | next_open |

## `two_layer_short_breakout.json`

| 指标 | 值 |
|---|---|
| base | v16 short below-all-MA |
| exit | TP5/SL2 |
| candidates | 2125 |

## `two_layer_tight_exit.json`

| 指标 | 值 |
|---|---|
| candidates | 15896 |

## `v13_train_diag_stats.json`

| 指标 | 值 |
|---|---|
| v13_train.name | v13_train |
| v13_train.n_label_files | 8467 |
| v13_train.n_empty_bg | 4520 |
| v13_train.n_pos_files | 3947 |
| v13_train.n_boxes | 4146 |
| v13_train.w_mean | 0.0568094 |
| v13_train.frac_right_ge_095 | 0.959961 |
| v13_train.frac_right_04_09 | 0.0255668 |
| v13_train.frac_w_le_03 | 0.00964785 |
| v13_train.frac_w_gt_10 | 0.00602991 |
| v13_train.frac_w_gt_15 | 0.000241196 |
| v13_train.multi_box_files | 195 |
| v13_train.render_checked | 80 |
| v13_train.frac_dark_bg_meanlt40 | 0 |

## `v15_revalidate_fair.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-22T16:50:44.467390+00:00 |
| protocol.true_tip_ma | full-MA |
| protocol.real_tip_ma | full-MA |
| protocol.conf | 0.3 |
| protocol.tip_edge_bars | 2 |
| protocol.preview | analysis/output/v13_real_tip_preview |
| legacy_unfair.slice_ma_tip_hit.v12 | 0.925 |
| legacy_unfair.slice_ma_tip_hit.v14 | 0.0333 |
| legacy_unfair.slice_ma_tip_hit.v15 | 0.0167 |
| legacy_unfair.tip_smoke_27.v12 | 0/27 |
| legacy_unfair.tip_smoke_27.v14 | 0/27 |
| legacy_unfair.tip_smoke_27.v15 | 0/27 |
| models.v12.weights | models/owner_best.pt |
| models.v14.weights | models/owner_v14_pad200.pt |

## `v16_holdout_verdict.json`

| 指标 | 值 |
|---|---|
| summary.tag | v16_holdout_verdict |
| summary.weights | models/owner_v16_tipuni_cold.pt |
| summary.window | 2026-05-04..2026-07-16 |
| summary.n_symbols | 15 |
| summary.bars_scanned | 87401 |
| summary.fired_raw | 6435 |
| summary.fire_per_1k_bars | 73.626 |
| summary.n_trades | 1206 |
| summary.win_rate | 0.2935 |
| summary.profit_factor | 0.784 |
| summary.mean_net_per_trade | -0.00234 |
| summary.total_net_units | -2.81618 |
| summary.cost | 0.0006 |

## `v16_judgment_v2.json`

| 指标 | 值 |
|---|---|
| exit | TP3/SL1 |
| v16_candidates | 4014 |

## `v16_realtip_gate.json`

| 指标 | 值 |
|---|---|
| generated_at | 2026-07-22T22:46:24.084963+00:00 |
| protocol.real_tip_ma | full-MA (same as collect_v13 / live) |
| protocol.conf | 0.3 |
| protocol.tip_edge_bars | 2 |
| protocol.tip_dense_hit_bars | 16 |
| models.v12.weights | models/owner_v16_tipuni_cold.pt |
| models.v15.weights | models/owner_v16_tipuni_cold.pt |

## `v6_holdout8_300.json`

| 指标 | 值 |
|---|---|
| tag | v6_holdout8_300 |
| side | short |
| n_symbols | 12 |
| n_fired | 3320 |
| n_trades | 793 |
| barriers | TP5.0/SL2.0/72bar |
| gross_mean | 0.00174 |
| gross_PF | 1.288 |
| win_rate | 0.3001 |

## `v6_holdout8_majors.json`

| 指标 | 值 |
|---|---|
| tag | v6_holdout8_majors |
| side | short |
| n_symbols | 12 |
| n_fired | 290 |
| n_trades | 77 |
| barriers | TP5.0/SL2.0/72bar |
| gross_mean | 5e-05 |
| gross_PF | 1.01 |
| win_rate | 0.2338 |

## `v6_short_replay.json`

| 指标 | 值 |
|---|---|
| tag | v6_short_replay |
| side | short |
| n_symbols | 8 |
| n_fired | 1554 |
| n_trades | 388 |
| barriers | TP5.0/SL2.0/72bar |
| gross_mean | -0.00131 |
| gross_PF | 0.832 |
| win_rate | 0.1753 |

## `yolo_short_10hv_6m_meta.json`

| 指标 | 值 |
|---|---|
| pilot | 10hv_6m |
| signal_time_lo | 2025-11-04 00:00:00+00:00 |
| signal_time_hi | 2026-05-04 00:00:00+00:00 |
| holdout_start | 2026-05-04T00:00:00+00:00 |
| vol_metric | std of daily close returns in window |
| filters.min_daily | 60 |
| filters.max_daily_std | 0.35 |
| filters.max_atr_pct_med | 0.15 |
| filters.min_bars_in_window | 500 |
| universe_scored | 251 |
| universe_skipped | 93 |

