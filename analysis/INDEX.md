# analysis/ 报告索引（自动生成,勿手改）

共 **221** 篇。重跑刷新:`PYTHONPATH=. .venv/bin/python scripts/gen_analysis_index.py`

> **动手前先在这里搜一遍**——这个索引存在的原因是:曾经差点重跑 owner 已标完的 2525 个
> 多空框(`p_owner_side_feature_verdict.md` 早有结论),也曾两个会话各自做了一遍同样的
> 视觉方向预检。**结论列是原文摘录,不是我的转述;空 = 机器提不出,不是没结论——去读原文。**

## 按日期倒序

| 日期 | 报告 | 标题 | 结论(原文摘录) |
|---|---|---|---|
| 2026-08-12 | [`p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md`](p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md) | Local Signal V2 Positive 语义纯度审计 PRE-REVIEW（2026-08-12） | 本轮 `DATA / SEMANTIC AUDIT ONLY` 的200张 Owner YES / NO / SKIP审核包已经完成，等待Owner人工审核。 |
| 2026-08-12 | [`p2_owner_short_gold_center_hardneg_r2_canary_20260812.md`](p2_owner_short_gold_center_hardneg_r2_canary_20260812.md) | P2 Owner确认误报第三训练臂与独立连续Canary（2026-08-12） | Owner于2026-08-11 23:14 CST授权的第三训练臂 |
| 2026-08-11 | [`local_signal_v2_progress.md`](local_signal_v2_progress.md) | Local Signal V2 — 进度一页纸 | P1 历史发现级对照已完成，B2 30 根固定因果窗胜出；生产级仍未验收。 |
| 2026-08-11 | [`p0_local_signal_v2_stagea_randomcrop_v1_report_20260811.md`](p0_local_signal_v2_stagea_randomcrop_v1_report_20260811.md) | Local Signal V2 Stage A 真实裁剪 P0 报告（2026-08-11） | Owner 已明确授权恢复交接文档中的 Stage A 离线预训练。新版 |
| 2026-08-11 | [`p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md`](p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md) | Local Signal V2 Stage B-from-A 数据验收报告（2026-08-11） | Owner 已确认 Stage B 的严格因果布局，并确认使用 Stage A `best.pt` 初始化微调。独立数据版本 |
| 2026-08-11 | [`p1_local_signal_v2_position_shortcut_20260811.md`](p1_local_signal_v2_position_shortcut_20260811.md) | Local Signal V2 位置 shortcut 纠错（2026-08-11） | Owner 观察正确：三张大图中的信号框全靠右不是拼图显示问题，而是 B2/P2 数据几何缺陷。 |
| 2026-08-11 | [`p1_local_signal_v2_report_20260811.md`](p1_local_signal_v2_report_20260811.md) | Local Signal V2 P1 局部因果窗口对照报告 | 2026-08-11 后续密度审计纠正**：此前把 proposal-pool 的 3,880 个 L1 fire rows 写成“交易/开单”是错误的，它们不是订单；但 B2 也确实放得过宽。conf=0.35 命中 56/357 easy-negative endpoints（15.69%... |
| 2026-08-11 | [`p1_local_signal_v2_stagea_gap_to_owner_target_20260811.md`](p1_local_signal_v2_stagea_gap_to_owner_target_20260811.md) | Local Signal V2：昨晚 3060 Stage A 与 Owner 最终目标差距复盘 | 结论：**保留全部旧资产，从昨晚 Stage A `best.pt` 继续精调；不从零推倒重来。 |
| 2026-08-11 | [`p1_local_signal_v2_stagea_position_eval_20260811.md`](p1_local_signal_v2_stagea_position_eval_20260811.md) | Local Signal V2 Stage A 训练与分位置诊断（2026-08-11） | Stage A 离线预训练已正常完成，且通过推理前冻结的真实 K 线位置门：模型不再只识别真实内容 |
| 2026-08-11 | [`p1_owner_eth_shortdelay_boundary_contract_20260811.md`](p1_owner_eth_shortdelay_boundary_contract_20260811.md) | ETH完美平台：竖线内核心与3–5根短延迟合同 |  |
| 2026-08-11 | [`p1_owner_eth_shortdelay_calibration30_20260811.md`](p1_owner_eth_shortdelay_calibration30_20260811.md) | P1 Owner ETH 短延迟动态窗口 30 张校准报告（2026-08-11） | 已按最新语义合同重渲染 30 张短延迟校准图：核心后文 3/4/5 根各 10 张，前文 6–10 根、 |
| 2026-08-11 | [`p1_owner_eth_shortdelay_codex_firstpass_20260811.md`](p1_owner_eth_shortdelay_codex_firstpass_20260811.md) | P1 Owner ETH 短延迟语义 Codex 一审（2026-08-11） | 基于Owner当前唯一明确的ETH空头参考，已逐张复核30张动态短窗校准样本，并形成保守四桶： |
| 2026-08-11 | [`p1_owner_eth_shortdelay_dynamic_review200_20260811.md`](p1_owner_eth_shortdelay_dynamic_review200_20260811.md) | P1 Owner ETH 空头动态短窗 200 张扩展、一审与逐图改框（2026-08-11） | Owner明确回复“确认”，冻结为只做空并认可前一轮绿/橙/红代表板方向；多头镜像排除、但不得 |
| 2026-08-11 | [`p1_owner_short_gold_center_recent2d_holdout_20260811.md`](p1_owner_short_gold_center_recent2d_holdout_20260811.md) | Owner-short compact YOLO 最近2天全市场回放（2026-08-11） | 本次按Owner在对话中的明确要求读取最近48小时数据，登记为该配置第 **1** 次消耗holdout。 |
| 2026-08-11 | [`p2_owner_short_gold_center_hardneg_arm_20260811.md`](p2_owner_short_gold_center_hardneg_arm_20260811.md) | P2 Owner-short compact YOLO Hard-Negative第二训练臂（2026-08-11） | 已按交接规范§6完成第二训练臂的数据构建：train为`1143 positive + 1143 easy negative + 2286 hard negative`，总负正比 **3:1**，hard占训练负样本 **66.67%**。 |
| 2026-08-11 | [`p2_owner_short_gold_center_hardneg_canary_20260811.md`](p2_owner_short_gold_center_hardneg_canary_20260811.md) | P2 Owner-short Hard-Negative重训与连续密度Canary（2026-08-11） | Owner于2026-08-11 16:12 CST明确授权的run |
| 2026-08-11 | [`p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md`](p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md) | P2 Owner-short Hard-Negative Canary 331事件审核包（2026-08-11） | 第二训练臂在独立pre-holdout连续canary产生的 **331个去重事件已全部逐张渲染**，覆盖140个币， |
| 2026-08-11 | [`p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md`](p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md) | P2 Owner确认误报第三训练臂数据审计（2026-08-11） | 第三训练臂数据集已构建并通过技术检查，**尚未启动训练**。 |
| 2026-08-10 | [`p0_local_signal_v2_stageb_strictneg_v2_report.md`](p0_local_signal_v2_stageb_strictneg_v2_report.md) | P0 修复 — Local Signal V2 Stage B strict-negative V2 | 旧 `datasets/local_signal_v2_stageb` 的正样本按时间切分，但负样本只是继承 split 名称，实际从整个 pre-holdout 历史随机抽取；原审计又只检查正样本，因此产生了错误的 P0 全绿。 |
| 2026-08-10 | [`p1_b2_short_l2_backtest_20260811.md`](p1_b2_short_l2_backtest_20260811.md) | Local Signal V2 B2：候选密度与收益诊断 |  |
| 2026-08-10 | [`p1_local_signal_v2_prereg_20260810.md`](p1_local_signal_v2_prereg_20260810.md) | P1 局部因果窗口对照预注册 | 统一使用 pre-holdout 的时间后移 validation 事件；禁止读取 `>=2026-05-04`。 |
| 2026-08-10 | [`p_w20_manifest_traceability_20260810.md`](p_w20_manifest_traceability_20260810.md) | w20 / lsv2 数据集可追溯性与可复现性审计 — 2026-08-10 |  |
| 2026-08-07 | [`p0_local_signal_v2_audit_20260807.md`](p0_local_signal_v2_audit_20260807.md) | P0 — 局部信号 V2 交接规范：旧管线审计、基线冻结与因果门测量 | 规范描述的 V2 管线**不是从零开始——它今天凌晨已经在本仓库跑通了一轮 |
| 2026-08-07 | [`p0_local_signal_v2_stageb_report.md`](p0_local_signal_v2_stageb_report.md) | P0 — Local Signal V2 Stage B：因果数据集重建与硬门槛通过 | Stage A（`dense_owner_w20_midbox`）P0 **失败**（7 门过 3）。 |
| 2026-08-07 | [`p1_local_signal_v2_stageb_cold_report.md`](p1_local_signal_v2_stageb_cold_report.md) | P1 — Local Signal V2 Stage B 冷启动（owner_lsv2_stageb_cold） | P0 通过后的 **P1 冷启动完成**。 |
| 2026-08-07 | [`p_w20_midbox_tip_backtest_20260807.md`](p_w20_midbox_tip_backtest_20260807.md) | w20 midbox tip 回测裁决 — 2026-08-07 | Owner 2026-08-07 明确批准：ATR 障碍 TP/SL + 全市场 tip 扫描 + matched control 置换 + **holdout**。 |
| 2026-08-04 | [`p_mtf_yolo_l2_bridge_prep_20260804.md`](p_mtf_yolo_l2_bridge_prep_20260804.md) | 小周期 YOLO → 冻结 L2 因果桥准备报告 — 2026-08-04 | 可以把 1m/2m/3m/5m 完整窗口 YOLO 的候选送入历史 v11 LightGBM，再把通过候选路由到下一 15m/30m 边界做**研究测试**；但旧 PF6.61 不能继承。旧结果来自 YOLO + 回归判断层整链，而且 full-window 框时间曾被回填成更早的信号时间。新... |
| 2026-08-03 | [`p0_baseline_audit_20260803.md`](p0_baseline_audit_20260803.md) | P0.0 基线审计 —— 仓库现状 vs Grok Build 接管计划 | 计划书 00 页称"当前 q90 阈值并不等于运行时 top-decile;固定门在 val 放行约 91.2%"。 |
| 2026-08-03 | [`p0_independent_acceptance_20260803.md`](p0_independent_acceptance_20260803.md) | P0 独立验收报告（2026-08-03） | `p0_independent_acceptance = accepted`，允许进入 P1-DATA。 |
| 2026-08-03 | [`p0_runtime_parity_audit_20260803.md`](p0_runtime_parity_audit_20260803.md) | P0 Runtime Parity 审计（2026-08-03） | REJECTED：当前 `models/ACTIVE` 不是 2026-07-30 研究优胜配置，研究结论不得转移。 |
| 2026-08-03 | [`p0_safety_protocol_repair_20260803.md`](p0_safety_protocol_repair_20260803.md) | P0-SAFETY short 协议修复报告（2026-08-03） | P0-SAFETY 本地验收通过；当前策略仍不可执行。 |
| 2026-08-03 | [`p1_preholdout_dataset_rebuild_20260803.md`](p1_preholdout_dataset_rebuild_20260803.md) | P1-DATA：pre-holdout immutable short L2 dataset 重建验收 | P1-DATA = accepted。** 已从冻结的 pre-holdout L1 proposal ledger 重建一份 |
| 2026-08-03 | [`p2_l2_audit_and_prereg_20260803.md`](p2_l2_audit_and_prereg_20260803.md) | P2-L2 只读审计与预注册（训练前 Owner 门） | P1 immutable dataset 的 P2 输入门通过。Owner 在对话中以“批准”确认了上一条消息列明的 |
| 2026-08-03 | [`p2_l2_preholdout_validation_20260803.md`](p2_l2_preholdout_validation_20260803.md) | P2-L2：immutable P1 dataset 训练与 pre-holdout 验收 | P2-L2 = REJECTED。** 训练与验证流程完整执行，但模型没有形成可部署的固定门： |
| 2026-08-03 | [`p2m_readonly_mechanism_audit_20260803.md`](p2m_readonly_mechanism_audit_20260803.md) | P2-M：ATR 尺度与形态关联的只读机制审计 |  |
| 2026-08-03 | [`p2r_readonly_root_cause_audit_20260803.md`](p2r_readonly_root_cause_audit_20260803.md) | P2-R：P1 immutable 上的只读根因审计 |  |
| 2026-08-03 | [`p_attribution_23bp_vs_minus16bp_20260803.md`](p_attribution_23bp_vs_minus16bp_20260803.md) | 归因:+23.49bp 与 -15.91bp 的 44bp 差从哪来 | 那 44bp 差的最大来源是切分方案,不是数据、不是特征语义。 |
| 2026-08-03 | [`prereg_attribution_20260803.md`](prereg_attribution_20260803.md) | 预注册:+23.49bp 与 -15.91bp 的归因 |  |
| 2026-08-03 | [`week_plan_20260803.md`](week_plan_20260803.md) | 一周执行计划（2026-08-03 → 08-09） |  |
| 2026-07-31 | [`p_gpt_architecture_review_20260731.md`](p_gpt_architecture_review_20260731.md) | fable-trading 架构与方法学审阅（2026-07-31） |  |
| 2026-07-31 | [`p_l2_v10_reg_freeze_20260731.md`](p_l2_v10_reg_freeze_20260731.md) | L2 切 v10 池回归 · 冻结与回测分析报告（2026-07-31） |  |
| 2026-07-31 | [`p_window_200_rationale.md`](p_window_200_rationale.md) | 检测窗为什么是 200 根 K 线？合理吗？如何提高检出准确度 | 问题 \| 答案 \| |
| 2026-07-30 | [`STATE_20260730.md`](STATE_20260730.md) | 项目状态与交接 · 2026-07-30 | ``` |
| 2026-07-30 | [`arch_overview_20260730.md`](arch_overview_20260730.md) | fable-trading 架构与现状总览（2026-07-30） |  |
| 2026-07-30 | [`eth3m_short_pilot_v2_cls_maintenance_plan.md`](eth3m_short_pilot_v2_cls_maintenance_plan.md) | ETH 3m v2 分类诊断脚本维护例外 |  |
| 2026-07-30 | [`evening_checklist_20260730.md`](evening_checklist_20260730.md) | 本晚问题梳理与处理清单（2026-07-30 → 07-31） | 类别 \| 结论 \| |
| 2026-07-30 | [`p_eth3m_short_pilot_v2_cls_diag_20260730.md`](p_eth3m_short_pilot_v2_cls_diag_20260730.md) | ETH 3m short-start v2 图像分类诊断训练报告 | 结论：**FAIL（静态 val 第一门失败） |
| 2026-07-30 | [`p_judgment_maker_cost_on_regtop.md`](p_judgment_maker_cost_on_regtop.md) | 选项 A 执行：回归 top 子集上的 maker 成本压降实测 |  |
| 2026-07-30 | [`p_judgment_maker_trial_a2_plan.md`](p_judgment_maker_trial_a2_plan.md) | A2 实施计划：隔离 maker 试错桶（VPS 小仓验证） |  |
| 2026-07-30 | [`p_judgment_reg_whitebox.md`](p_judgment_reg_whitebox.md) | 回归预测 net + 白盒规则（推荐 1+3 验证） | 结论**：几条 if 规则/线性打分**无法近似模型**。模型学到的非线性组合（波动 + 范围 + 量能 + 多个 alpha）不是简单阈值能覆盖的。 |
| 2026-07-30 | [`p_judgment_topdecile_profile_v10.md`](p_judgment_topdecile_profile_v10.md) | 剖开顶十分位：v10 池判断层 top-decile 特征画像与匹配对照 |  |
| 2026-07-30 | [`p_judgment_topdecile_target_ab.md`](p_judgment_topdecile_target_ab.md) | A+B 实验：把「顶十分位」本身作为判断层新目标 | 结论（B）**：两个目标重合度低，top 明显更「极端波动+弱势」，与剖开画像一致。owner 标注包含大量「非顶但被标」的样本。 |
| 2026-07-29 | [`backlog_future_optimizations.md`](backlog_future_optimizations.md) | 未来优化 backlog（现在不做） | 结论**:配对贡献 +2.42bp(t=1.13,8/15 折),**置换检验 p=0.0333 未过 0.01 门槛**。 |
| 2026-07-29 | [`eth3m_short_pilot_v2a_maintenance_plan.md`](eth3m_short_pilot_v2a_maintenance_plan.md) | ETH 3m pilot v2a 大脚本维护例外与拆分计划 |  |
| 2026-07-29 | [`p_eth_3m_calibration240_preview.md`](p_eth_3m_calibration240_preview.md) | ETH 3m 双视图 240 张校准包预览 |  |
| 2026-07-29 | [`p_eth_3m_entry_timing_calibration30.md`](p_eth_3m_entry_timing_calibration30.md) | ETH 3m 提前入场线 30 张校准包 |  |
| 2026-07-29 | [`p_eth_3m_short_pilot_v1.md`](p_eth_3m_short_pilot_v1.md) | ETH 3m 做空检测器 pilot v1 — 数据质量与训练启动记录 | 数据链路通过了结构性检查，但 **pilot 最终验收失败**：连续严格 OOS 在 774 根 eligible bars 中开火 772 根（99.74%），没有形成稀疏事件。最终训练池为 183 张（76 正 / 107 负），已按事件做严格时间切分。3060 队列在 owner 确认后... |
| 2026-07-29 | [`p_eth_3m_short_pilot_v1_backtest.md`](p_eth_3m_short_pilot_v1_backtest.md) | ETH 3m 专用做空模型 pilot v1 — 因果回放报告 | 本轮不通过。** 严格时序 OOS 的 774 根盘口中，模型在 772 根上画了 tip 框，原始开火率 |
| 2026-07-29 | [`p_eth_3m_short_pilot_v2_dataset.md`](p_eth_3m_short_pilot_v2_dataset.md) | ETH 3m short-start pilot v2 数据集审计 | v2 已按 owner 明确证据重构并通过独立结构验证，但只够做诊断 pilot。** train/val 共有 |
| 2026-07-29 | [`p_eth_3m_v10_owner_labels_timing.md`](p_eth_3m_v10_owner_labels_timing.md) | ETH 3m v10 owner 标注后的迟到诊断 | 人工标注确认 v10 确实能找到一部分目标形态：93/200 张为“是”（46.5%）。但这 93 张不能直接当入场可用正例：框的横向中位跨度为 36 分钟，到开火时从框内最高收盘到信号收盘已经下跌中位 4.47 个 3m ATR；93/93 在信号端都位于六条均线下方。 |
| 2026-07-29 | [`p_eth_3m_v10_prebox200.md`](p_eth_3m_v10_prebox200.md) | ETH 3m · v10 有框预标 200 张 |  |
| 2026-07-29 | [`p_eth_3m_v10_prelabels_3m.md`](p_eth_3m_v10_prelabels_3m.md) | ETH 3m × v10 最近三个月预打标预览 | 已在 `2026-04-29 07:45`～`2026-07-29 04:45 UTC` 的 ETH-USDT-SWAP 3m K 线上，等距抽取 **2,000 / 43,621** 个可扫盘口锚点。 |
| 2026-07-28 | [`p_20260728_four_tracks.md`](p_20260728_four_tracks.md) | 2026-07-28 四件事的结果 + 判断层判定 |  |
| 2026-07-28 | [`p_20260728_matched_control_verdict.md`](p_20260728_matched_control_verdict.md) | 对照组终判：检测器的边 ≈ 成本，而金标本身没有一个盘口样本 — 2026-07-28 |  |
| 2026-07-25 | [`p_short_tip_v1b_detect1000_shortish.md`](p_short_tip_v1b_detect1000_shortish.md) | tip_v1b 1000 框 → 空头观感过滤包（S3 补丁，不 promote） |  |
| 2026-07-24 | [`p_how_to_unlock_label_to_trade_chain.md`](p_how_to_unlock_label_to_trade_chain.md) | 如何打通「打标 → 特征/因子 → 可交易」— 2026-07-24 | 链路没断在「缺因子」或「缺出场旋钮」，断在「标签语义不可部署」+「用错裁判」+「regime 不迁移」。 |
| 2026-07-24 | [`p_it14_visual_direction_precheck.md`](p_it14_visual_direction_precheck.md) | IT-14 · tip 窗图像素是否携带方向信号（冻结 COCO embed 预检） | 红灯。** 视觉 embed 三期 held-out AUC 均 ≤0.507、top-decile 方向 PF 均 ≤1.096， |
| 2026-07-24 | [`p_it15_tip_remap.md`](p_it15_tip_remap.md) | IT-15 · tip remap（框右缘 → 局部密度谷）— 诊断有用，不可当部署边 | 诊断通过、部署否决。 |
| 2026-07-24 | [`p_judgment_layer_lab.md`](p_judgment_layer_lab.md) | 判断层重构实验室(活文档,持续迭代)— 起于 2026-07-24 |  |
| 2026-07-24 | [`p_live_readiness_checklist.md`](p_live_readiness_checklist.md) | 可上实盘检查清单（判断层重构 — 停在 Owner 点头门前） | 结论：距「只差 Owner 点头」仍差 G0–G4 / G6。** 诚实停点 = 告警/观察价值可保留； |
| 2026-07-24 | [`p_owner_side_short_tip_v1b.md`](p_owner_side_short_tip_v1b.md) | owner_side_short_tip_v1b — tip-smoke 诚实评估（不 promote） | 项 \| 结果 \| |
| 2026-07-24 | [`p_short_judgment_100_6m_reg.md`](p_short_judgment_100_6m_reg.md) | short 100×6m 回归单切（发现级，未 holdout / 未 promote） | 扩到 **n=25602**（接近 v11 候选量级哲学）后，单切 top-decile 净仍 **+0.471%**（n=510），略好于 30×6m 的 +0.371%；但 **Spearman 从 0.149 塌到 0.016**，置换 p 从 0.001 松到 **0.037**，va... |
| 2026-07-24 | [`p_short_judgment_100_6m_reg_walkforward.md`](p_short_judgment_100_6m_reg_walkforward.md) | short 100×6m 回归 — 5-fold walkforward（发现级，未 holdout） |  |
| 2026-07-24 | [`p_short_judgment_30_6m_reg_walkforward.md`](p_short_judgment_30_6m_reg_walkforward.md) | short 30×6m 回归 — 5-fold walkforward（发现级，未 holdout） |  |
| 2026-07-24 | [`p_short_judgment_refactor_v1.md`](p_short_judgment_refactor_v1.md) | Short 判断层重构 v1：结构性 short-only 路径 + 特征方向镜像单变量实验 |  |
| 2026-07-24 | [`p_short_judgment_refactor_v2.md`](p_short_judgment_refactor_v2.md) | Short 判断层重构 v2：扩币（30×6m）镜像基线 + top-K 单变量 |  |
| 2026-07-24 | [`p_short_judgment_reg_align_v11.md`](p_short_judgment_reg_align_v11.md) | 纠偏：short 判断层对齐 v11 回归主链 | 是的，之前偏了。** short 试点曾落到 binary 小样本 + 把镜像当胜负实验；现已改回与 v11 同构的回归主链。本轮 30×6m 回归：val top-decile 净 **+0.371%**（n=150，扣 0.2%）、Spearman **0.149**、val-q90=**... |
| 2026-07-24 | [`p_short_only_backtest_tip_v1b_5_6m.md`](p_short_only_backtest_tip_v1b_5_6m.md) | SHORT 回测：tip_v1b × 5 流动性币 × 6m（pre-holdout） | tip_v1b short YOLO 在 **5 币 × [2025-11-04, 2026-05-04)** 窗上训出 val AUC **0.599**、置换 **p≈0.009**、top-decile（n=24）扣 0.2% 后净收益 **+0.062%**——数字方向对，但 **va... |
| 2026-07-24 | [`p_short_only_pipeline.md`](p_short_only_pipeline.md) | 只做空全链路作战计划（short-only pipeline） |  |
| 2026-07-24 | [`p_short_tip_v1b_detect1000.md`](p_short_tip_v1b_detect1000.md) | tip_v1b 实际 K 线 ~1000 框包（S3，不 promote） |  |
| 2026-07-24 | [`p_tip_mapping_owner_intent.md`](p_tip_mapping_owner_intent.md) | tip 映射审计：`box_right_frac≈0.5` 是否冤枉 Owner「框=tip」 | 两件事要分开： |
| 2026-07-24 | [`project_management_plan_20260724.md`](project_management_plan_20260724.md) | fable-trading 项目管理计划（2026-07-24） |  |
| 2026-07-24 | [`todo_short_only_pipeline.md`](todo_short_only_pipeline.md) | Short-only 链路待办 |  |
| 2026-07-23 | [`p_base_rate_dense_verdict.md`](p_base_rate_dense_verdict.md) | 密集几何 base rate 终判:信号真实但边际,成本才是杀手 — 2026-07-23 |  |
| 2026-07-23 | [`p_chain_failure_attribution.md`](p_chain_failure_attribution.md) | 密集链路失败归因 — 哪一层是主因 — 2026-07-23 | 层 \| 支持度 \| 是否已排除 \| 一句话 \| |
| 2026-07-23 | [`p_direction_select_base_rate.md`](p_direction_select_base_rate.md) | 因果择向 base rate — 2026-07-23 | 变体 \| 边 \| n \| 胜率 \| 净@maker \| PF@maker \| PF@0.2% \| |
| 2026-07-23 | [`p_e3_sparse_and_two_stage.md`](p_e3_sparse_and_two_stage.md) | E3 稀疏化 + 两段式确认 — 2026-07-23 | 实验 \| 裁决 \| |
| 2026-07-23 | [`p_entry_align_and_regime.md`](p_entry_align_and_regime.md) | E1 入场对齐 owner short + E2 regime 门 — 2026-07-23 | 实验 \| 裁决 \| |
| 2026-07-23 | [`p_entry_timing_close_vs_next.md`](p_entry_timing_close_vs_next.md) | 入场时机：signal_close vs next_open — 2026-07-23 | 变体 \| 边 \| n \| 胜率@n \| PF@maker next \| PF@maker close \| Δ(close−next) \| PF@0.2% next \| PF@0.2% close \| |
| 2026-07-23 | [`p_latest_code_review_20260723.md`](p_latest_code_review_20260723.md) | 最新代码审查 — 2026-07-23 |  |
| 2026-07-23 | [`p_launch_entry_base_rate.md`](p_launch_entry_base_rate.md) | 启动入场 vs 盘整中入场：因果 base rate 单变量对照 — 2026-07-23 | 回答 owner：「启动那一刻 + 跟随突破方向」相对「密集第 5 根（盘整中）」是否抬高 PF， |
| 2026-07-23 | [`p_launch_entry_long_short.md`](p_launch_entry_long_short.md) | 启动入场：强制多空分边 base rate — 2026-07-23 | 回答 owner：「多空没区分好」——上一轮把跟向多空合成一行 PF；本轮强制分边后，启动 |
| 2026-07-23 | [`p_owner_label_feature_verdict.md`](p_owner_label_feature_verdict.md) | Owner 标框手法 → 因果特征 → train base rate 裁决 — 2026-07-23 | 纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；TP5/SL2/72bar；成本同时报 |
| 2026-07-23 | [`p_owner_side_feature_verdict.md`](p_owner_side_feature_verdict.md) | Owner 分边标框 → 因果特征 → train base rate 裁决 — 2026-07-23 | 纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；long→`label_candidate`、 |
| 2026-07-23 | [`p_owner_side_rich_features_verdict.md`](p_owner_side_rich_features_verdict.md) | Owner 扩特征分边裁决 — 2026-07-23 | 纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；long→`label_candidate`、 |
| 2026-07-23 | [`p_samesource_judgment_verdict.md`](p_samesource_judgment_verdict.md) | 同源判断层 + 新特征:walk-forward 证伪"稳健 edge" — 2026-07-23 夜 | "双均线密集启动"在实时盘口、扣成本、TP5/SL2 结构下,没有稳健可交易 edge。 |
| 2026-07-23 | [`p_short_trend_ab.md`](p_short_trend_ab.md) | 空边趋势出场 A/B — 稳健性 + owner short 对照 — 2026-07-23 | A — 月度口径稳健过线，季度有集中张力。 |
| 2026-07-23 | [`p_short_trend_holdout7.md`](p_short_trend_holdout7.md) | Holdout #7 — A 因果空边趋势出（no_tp / trail4）— 2026-07-23 | 证伪。** train 过线的两档趋势出场，在 holdout 上全部塌到 ~1.0： |
| 2026-07-23 | [`p_tip_eval_fairness.md`](p_tip_eval_fairness.md) | tip 验收公平性审计 — tip-smoke / tip_hit 会不会冤假错案？ | 结论 \| 为何仍站 \| |
| 2026-07-23 | [`p_trend_exit_base_rate.md`](p_trend_exit_base_rate.md) | 趋势出场 base rate — 2026-07-23 | 空边：趋势出场抬过 1.3。** 三套过线—— |
| 2026-07-23 | [`p_v13_real_tip_collect_plan.md`](p_v13_real_tip_collect_plan.md) | v13 — 收集 live 真实 tip 成败图（计划） |  |
| 2026-07-23 | [`p_v15_dataset_confound.md`](p_v15_dataset_confound.md) | v15 败因定论:正负样本来自两条渲染管线(风格捷径)— 2026-07-23 | v15(及 v14)训练集的**正样本全部是 `_pad200` 重渲图,负样本全部是旧式原图**—— |
| 2026-07-23 | [`p_v15_revalidate_fair.md`](p_v15_revalidate_fair.md) | v15 发现级公平重验 — 2026-07-23 | 仍否决 promote v15。 |
| 2026-07-23 | [`p_v15_tip_val.md`](p_v15_tip_val.md) | v15 tip-val（Hypothesis B）中期裁决 — 2026-07-23 | 纪律**：未 promote `owner_best` / ACTIVE / frozen；未评 holdout；未清 forward_log。 |
| 2026-07-23 | [`p_v16_holdout_verdict.md`](p_v16_holdout_verdict.md) | v16 holdout 终审:纯检测亏损,判断层反预测 — 2026-07-23 |  |
| 2026-07-23 | [`p_v16_tipuni_train.md`](p_v16_tipuni_train.md) | v16 tipuni(统一管线冷启动)训练与金标验收 — 2026-07-23 | 结论:不上线。** 主线维持 detector=none。 |
| 2026-07-22 | [`p_frontend_viz_opt.md`](p_frontend_viz_opt.md) | 前端可视化优化 — 真落地 + 风格收敛 | 第一轮（`4b0c403`）把 Tabulator / 状态灯 / explore 框落地后，Owner 反馈 **整体风格变土**——不是功能错，是视觉像「AI 监控大屏」：6 格状态卡、midnight 表头、seg pill 滤镜、侧栏调试区喧宾夺主。 |
| 2026-07-22 | [`p_overnight_20260722.md`](p_overnight_20260722.md) | 夜间工作纪要 — 2026-07-22 |  |
| 2026-07-22 | [`p_pad200_cut_audit.md`](p_pad200_cut_audit.md) | pad200 切割审计 — Owner「框不对」— 2026-07-22 |  |
| 2026-07-22 | [`p_pad200_regression_why.md`](p_pad200_regression_why.md) | 为什么「昨天修过 stem」v13 还是错窗 — 2026-07-22 |  |
| 2026-07-22 | [`p_project_overview_20260722.md`](p_project_overview_20260722.md) | 项目总览（给 Owner）— 2026-07-22 夜 |  |
| 2026-07-22 | [`p_real_tip_collect_started.md`](p_real_tip_collect_started.md) | 真实 tip 成败金标小样 — 已开干（2026-07-22 夜） | 本机 K 线停在 07-16，盖不住账本信号 → **在 VPS 上采集**后拉回本机。 |
| 2026-07-22 | [`p_side_tools_landed.md`](p_side_tools_landed.md) | 本机旁路工具集落地 — 发现级收尾 |  |
| 2026-07-22 | [`p_v13_pad200_train.md`](p_v13_pad200_train.md) | v13 pad200 终局 + H-DET-1 tip 对照 — 2026-07-22 | 权重 \| best ep \| P \| R \| mAP50 \| mAP50-95 \| |
| 2026-07-22 | [`p_v13_why_bad_train.md`](p_v13_why_bad_train.md) | 为什么 v13 训这么差？训练集诊断 — 2026-07-22 |  |
| 2026-07-22 | [`p_v14_failure_rootcause.md`](p_v14_failure_rootcause.md) | v14 tip 仍失败 — 根因分析（有证据）— 2026-07-22 | v14 不是「标签又坏了」**（MAD-on 抽检错窗≈0；存档 pad200 与 `process_pad200` 重渲 **MAD=0**）。 |
| 2026-07-22 | [`p_v14_pad200_rebuild.md`](p_v14_pad200_rebuild.md) | v14 pad200 重建（MAD-on）— 2026-07-22 |  |
| 2026-07-22 | [`p_v14_pad200_train.md`](p_v14_pad200_train.md) | v14 pad200（MAD-on）终局 + tip 对照 — 2026-07-22 |  |
| 2026-07-22 | [`p_v14_sample30.md`](p_v14_sample30.md) | v14 pad200 抽检 30 张 + okx 错窗小样 — 2026-07-22 | 结论：可以放心 sync 去 Windows。** `mad_gate=true`；okx 错窗抽检 **0**；未见 v13 式残留错框。未 sync、未开训、未 promote。 |
| 2026-07-22 | [`p_wuzao_a_tier_done.md`](p_wuzao_a_tier_done.md) | wuzao A 档落地短报（2026-07-22 夜） |  |
| 2026-07-22 | [`p_wuzao_more_useful.md`](p_wuzao_more_useful.md) | 无噪 topics：前端之外还有哪些对本仓真正好用 | 昨夜 A 档把「能立刻落地」偏成了 **LWC/叠框/LS/规格**——对，但不够。 |
| 2026-07-22 | [`p_wuzao_topics_scan.md`](p_wuzao_topics_scan.md) | 无噪（wuzao）全站 topics 扫描 — 对本仓可迁移性 | 结论 \| 内容 \| |
| 2026-07-22 | [`p_yolo_dense_hypotheses.md`](p_yolo_dense_hypotheses.md) | YOLO 均线密集检测层假设簇（H-DET）— 发现级汇总 | 调度/阈值不是解药；几何训练分布才是。** tip-only 与 TIP_CONF 已证伪抬 tip_fire。 |
| 2026-07-22 | [`p_yolo_external_sources.md`](p_yolo_external_sources.md) | 外源调研：YOLO「均线密集 / 盘口 tip」可迁移点子 | 外面**没有**「盘口 tip 均线密集」现成解药。公开物分成三类： |
| 2026-07-22 | [`p_yolo_while_v13_trains.md`](p_yolo_while_v13_trains.md) | v13 训练期间可做项 — 短报告（2026-07-22） |  |
| 2026-07-21 | [`night_report_20260721.md`](night_report_20260721.md) | 晨报 / 批次状态（2026-07-21） |  |
| 2026-07-21 | [`p_box_to_bar_lag.md`](p_box_to_bar_lag.md) | 框→bar 滞后机制（EDEN / KORU）— 2026-07-21 | 根因是几何语义错位，不是映射 bug。 |
| 2026-07-21 | [`p_chartscanai_review.md`](p_chartscanai_review.md) | ChartScanAI 详细评测 — 对 fable-trading 有什么用 | 对「盘口 tip 认不出」没有直接帮助。** ChartScanAI 和本仓撞上的是同一类坑：框往往标在形态**已经走完**之后，右缘/盘口几乎点不着火。社区 issue 明确写「只事后认」「实时信号滞后」——这正是本仓 tip 出生率≈0 的同构失败模式，不是解药。 |
| 2026-07-21 | [`p_execution_slippage.md`](p_execution_slippage.md) | 执行折扣 / 滑点实测（2026-07-21） | 无法从当前台账可靠估计「成交价相对账本价」的 bp 滑点。 |
| 2026-07-21 | [`p_github_optimize_candidates.md`](p_github_optimize_candidates.md) | GitHub 开源候选 — 对本仓真实痛点的第二轮筛选 | 何时 \| 值得做什么 \| 不值得做什么 \| |
| 2026-07-21 | [`p_realtime_yolo_within_bar.md`](p_realtime_yolo_within_bar.md) | YOLO「bar 内实时推理」路线图 — 2026-07-21 | 真正卡 tip_fresh 的不是「推理引擎不够快」，而是「模型在无后文 tip 窗上贴边框出生率≈0」+「信号龄从 bar open_time 起算」的结构算术。 |
| 2026-07-21 | [`p_tip_only_smoke.md`](p_tip_only_smoke.md) | tip-only 扫描冒烟诊断 — 2026-07-21 | 不要永久改主线为 tip-only。** tip 调度本身几乎不抬 tip_fire；根因仍是模型在 |
| 2026-07-21 | [`p_tip_subset_val.md`](p_tip_subset_val.md) | p_tip_subset_val — tip 可检子集 vs 全量基线（严格 val 窗） | 实盘群体折扣系数（tip_strict 净收益 / 全量净收益，val，成本 0.3%）= 0.0465。 |
| 2026-07-20 | [`forward_mainline_status_20260720.md`](forward_mainline_status_20260720.md) | 前向 / 主线诚实状态摘要（2026-07-20） |  |
| 2026-07-20 | [`p2a_v12_mainline_cutover.md`](p2a_v12_mainline_cutover.md) | 检测主线切 v12（owner 强制）— 2026-07-20 |  |
| 2026-07-20 | [`p_exit_parity.md`](p_exit_parity.md) | P-EXIT-PARITY：回测 vs 前向出场逻辑等价性验证（2026-07-20） |  |
| 2026-07-20 | [`p_v12_htip_eval.md`](p_v12_htip_eval.md) | H-TIP v12 评测（D1）— 2026-07-20 |  |
| 2026-07-20 | [`p_v12_score_shift.md`](p_v12_score_shift.md) | 路 C：检测 v12 × 判断 v11 冻结 —— val 窗小段重扫分数漂移测量 | covariate shift 极小，过阈率几乎不变，top-decile 净收益仍强正、无统计显著塌陷。 |
| 2026-07-20 | [`p_v12_shadow_start.md`](p_v12_shadow_start.md) | v12 影子启动记录 — 2026-07-20 |  |
| 2026-07-20 | [`p_weight_centric_val.md`](p_weight_centric_val.md) | p_weight_centric — score→size 连续仓位 vs 二元 all-in（严格 val 窗离线回测） | 分位分档映射（q90-95/q95-99/q99+ → 1x/1.5x/2x）在 val 窗把净收益从 +141.0% 提到 |
| 2026-07-20 | [`week_plan_20260720.md`](week_plan_20260720.md) | 一周执行计划(2026-07-20 → 07-27)— 交给 Grok 执行版 |  |
| 2026-07-19 | [`h_tip_plan.md`](h_tip_plan.md) | H-TIP — tip-firing for live YOLO |  |
| 2026-07-19 | [`p_forward_hindsight_20260719.md`](p_forward_hindsight_20260719.md) | 前向事后检出日结 — 2026-07-19 | 脉冲在 04:30–05:30 UTC（信号附近）**正常踩点**（`fable-forward.timer` 每 15m）。 |
| 2026-07-18 | [`p3_v11_pool_cutover.md`](p3_v11_pool_cutover.md) | p3 — v11 池判断层切换 ACTIVE |  |
| 2026-07-17 | [`p2a_hts_report.md`](p2a_hts_report.md) | H-TS — 检测层训练图时间切分实验 |  |
| 2026-07-17 | [`p2b_judgment_audit.md`](p2b_judgment_audit.md) | p2b — 判断层全面体检 + 两个前沿改造实验(J-1/J-2) |  |
| 2026-07-16 | [`p2a_lr_bug_audit.md`](p2a_lr_bug_audit.md) | p2a — 学习率 bug 审计与 v8 重训 | "干净尺子首次证实加数据有效: v6(4501)0.595 → v7(6501)0.625"** —— **撤回**。 |
| 2026-07-16 | [`p3_v8_pool_cutover.md`](p3_v8_pool_cutover.md) | p3 — 干净池(v8_chain)判断层切换 ACTIVE |  |
| 2026-07-15 | [`p15_h3_ma_exit.md`](p15_h3_ma_exit.md) | P1.5 H3：结构出场（收盘跌破 EMA21） |  |
| 2026-07-15 | [`p15_h4_time_decay.md`](p15_h4_time_decay.md) | P1.5 H4：时间衰减紧缩出场 |  |
| 2026-07-15 | [`p15_h5_vol_adaptive.md`](p15_h5_vol_adaptive.md) | P1.5 H5：波动率自适应障碍 |  |
| 2026-07-15 | [`p2a_ab_leak_correction.md`](p2a_ab_leak_correction.md) | A/B 泄漏更正与干净检验（2026-07-15） |  |
| 2026-07-15 | [`p2a_yolo_critical_path_ab.md`](p2a_yolo_critical_path_ab.md) | A/B: YOLO候选源 vs 规则候选源（SWAP，发现级 val-only） |  |
| 2026-07-15 | [`p2a_yolo_mainline_cutover.md`](p2a_yolo_mainline_cutover.md) | YOLO 主线切换（owner 2026-07-15） |  |
| 2026-07-15 | [`p2b_factor_ic_vol.md`](p2b_factor_ic_vol.md) | H14/H17/H18 成交量因子三连 IC 筛选（SWAP 池, train/val） | 结论**：三因子均 **不进入** 单变量增益验证队列。负结果保留：成交量方向假说在「密集启动候选池 + 72bar 前向收益」切片上尚未显现出可过线的线性秩相关。 |
| 2026-07-15 | [`p2b_h11_tiered.md`](p2b_h11_tiered.md) | H11 市值/流动性分层模型（SWAP 24h 成交额中位数二分） |  |
| 2026-07-15 | [`p2b_h13_btc_regime.md`](p2b_h13_btc_regime.md) | H13 BTC 大盘状态共享特征（SWAP 池, train/val） |  |
| 2026-07-15 | [`p2b_h15_quality.md`](p2b_h15_quality.md) | H15 密集质量二阶特征 IC 筛选（SWAP 池, train/val） |  |
| 2026-07-15 | [`p2b_h8_30m_grid.md`](p2b_h8_30m_grid.md) | H8 后续：30m 网格 TP{4,5,6}×horizon{48,60,72} |  |
| 2026-07-15 | [`p2b_low_tf_backtest_report.md`](p2b_low_tf_backtest_report.md) | 低周期回测：1m / 2m / 3m / 5m vs 15m |  |
| 2026-07-15 | [`p2b_ml_layer_opt_summary.md`](p2b_ml_layer_opt_summary.md) | ML 层可优化方向 — 实测扫描总结 | 方向 \| 结论 \| |
| 2026-07-15 | [`p2b_ml_opt_rules_expanded_report.md`](p2b_ml_opt_rules_expanded_report.md) | ML 层优化扫描（YOLO 判断池，val-only） |  |
| 2026-07-15 | [`p2b_ml_opt_swap_tp5_report.md`](p2b_ml_opt_swap_tp5_report.md) | ML 层优化扫描（YOLO 判断池，val-only） |  |
| 2026-07-15 | [`p2b_ml_opt_yolo_report.md`](p2b_ml_opt_yolo_report.md) | ML 层优化扫描（YOLO 判断池，val-only） |  |
| 2026-07-15 | [`p2b_yolo_reg_active_cutover.md`](p2b_yolo_reg_active_cutover.md) | 判断层切 ACTIVE：YOLO + 回归 realized_ret |  |
| 2026-07-15 | [`p3_ml_opt_backtest_compare.md`](p3_ml_opt_backtest_compare.md) | 回测对照：二分类 vs 回归收益（YOLO 主线池） |  |
| 2026-07-15 | [`p3_yolo_mainline_backtest.md`](p3_yolo_mainline_backtest.md) | YOLO 主线整体回测（切流后，2026-07-15） |  |
| 2026-07-12 | [`p2b_hf_2m_3m_data_feasibility.md`](p2b_hf_2m_3m_data_feasibility.md) | 2m / 3m 高频影子数据可行性 | OKX 当前真实接口可直接返回 `2m` 和 `3m` K 线。仓库数据层已支持这两个周期，BTC/ETH |
| 2026-07-11 | [`lightgbm_system_and_tooling_review.md`](lightgbm_system_and_tooling_review.md) | LightGBM 判断层与工具接入评估 |  |
| 2026-07-11 | [`p2a_e21b_hsv0_report.md`](p2a_e21b_hsv0_report.md) | P2a E2.1b 全 HSV 关闭正式验收 | E2.1b 于 `2026-07-11 01:42:07 CST` 自然结束，exit 0，完成 40/40 epochs。训练配置 |
| 2026-07-11 | [`shadow_booster_framework_comparison.md`](shadow_booster_framework_comparison.md) | LightGBM / CatBoost / XGBoost / Ensemble 影子比较 | LightGBM 继续作为 ACTIVE。** 它已有完整冻结、解释、指纹和前向链路，单条本地评分最快； |
| 2026-07-11 | [`two_day_final_audit_20260711.md`](two_day_final_audit_20260711.md) | 两日任务最终审计（2026-07-11） | 两日执行清单的工程、检测终验、SAHI、VPS 和安全检查已经完成。系统能够更新全合约数据、 |
| 2026-07-10 | [`ma206_q80_shadow_24h_report.md`](ma206_q80_shadow_24h_report.md) | MA206 q80 影子 24 小时终验 | 首个不可变 ready 快照覆盖 `2026-07-10 10:30 UTC` 至 `2026-07-11 10:30 UTC`，恰好 |
| 2026-07-10 | [`oss_architecture_benchmark.md`](oss_architecture_benchmark.md) | 开源架构基准与隔离试点 | 当前不应把 fable 迁移到另一套交易或 MLOps 框架。最有价值的路径是借鉴成熟项目的 |
| 2026-07-10 | [`p25_daily_workflow_acceptance_20260710.md`](p25_daily_workflow_acceptance_20260710.md) | MA206 每日安全链验收（2026-07-10） | `update_okx → champion/H1 forward → digest dry-run → pipeline → VPS` 已用当前 MA206 数据完整跑通，并修复三处会破坏无人值守可信度的问题。Codex 每日自动化已更新为这条安全链；旧 Claude Telegram 任务... |
| 2026-07-10 | [`p25_local_acceptance_20260710.md`](p25_local_acceptance_20260710.md) | P2.5 本地验收（2026-07-10） | P2.5 本地只读控制台通过验收。token 鉴权有效，任务执行器保持关闭；实验、议程、任务、数据、模型、流水线六个视图在桌面与 390px 手机视口均可用。未开启实盘、VPS executor 或任何 holdout 读取。 |
| 2026-07-10 | [`p25_vps_acceptance_20260710.md`](p25_vps_acceptance_20260710.md) | P2.5 VPS 公网验收（2026-07-10） | 当前 MA206 项目流水线已部署到 `http://103.214.174.58:8642/` 并通过公网验收。匿名用户可查看脱敏只读七阶段状态；实验、模型、任务等 `/api/ops/*` 控制面仍要求 token；VPS 任务执行器保持关闭。 |
| 2026-07-10 | [`p2a_bad_images_pack.md`](p2a_bad_images_pack.md) | P2-11 偏 B · 坏图清单（Round 1 → E2） |  |
| 2026-07-10 | [`p2a_consistency_e21_vs_old_best.md`](p2a_consistency_e21_vs_old_best.md) | Consistency: E2.1 GT vs old yolo11s best.pt preds |  |
| 2026-07-10 | [`p2a_e1_xpad_report.md`](p2a_e1_xpad_report.md) | P2-11 E1 — 收紧 `x_pad_px`（12 → 6） |  |
| 2026-07-10 | [`p2a_e21_train_interim.md`](p2a_e21_train_interim.md) | YOLO E2.1 training interim (train EXITED) |  |
| 2026-07-10 | [`p2a_e21_train_report.md`](p2a_e21_train_report.md) | P2a YOLO E2.1 formal retrain report |  |
| 2026-07-10 | [`p2a_e2_max_dense_report.md`](p2a_e2_max_dense_report.md) | P2-11 E2 — 长段收核 `MAX_DENSE_BARS=24` |  |
| 2026-07-10 | [`p2b_ma206_mainline_migration.md`](p2b_ma206_mainline_migration.md) | P2b 判断层统一 SMA/EMA 20/60/120 | 2026-07-10 owner 明确推翻 07-09 的旧裁决，要求检测层、判断层及未来运行路径全部统一为 |
| 2026-07-10 | [`two_day_pre_final_audit_20260710.md`](two_day_pre_final_audit_20260710.md) | 两日任务预终审（2026-07-10） | Todo 1-6 与 Todo 9 已有可复核的实现、测试或实机证据。Todo 7 必须等待当前 |
| 2026-07-09 | [`p15_h10_short_report.md`](p15_h10_short_report.md) | P1.5 R2：H10 做空侧镜像验证 |  |
| 2026-07-09 | [`p15_h1_h2_exit_report.md`](p15_h1_h2_exit_report.md) | P1.5 R3：H1/H2 出场复合验证 |  |
| 2026-07-09 | [`p15_h9_report.md`](p15_h9_report.md) | P1.5 R1'：H9 高层趋势过滤复测与推广 |  |
| 2026-07-09 | [`p2_data_audit_report.md`](p2_data_audit_report.md) | P2-12 数据质量审计 |  |
| 2026-07-09 | [`p2a_label_audit_round1.md`](p2a_label_audit_round1.md) | P2-11 YOLO Label Audit Round 1 |  |
| 2026-07-09 | [`p2b_mtf_report.md`](p2b_mtf_report.md) | P1.5 R4：H7/H8 多时间框架池 |  |
| 2026-07-08 | [`p2b_v2_report.md`](p2b_v2_report.md) | 阶段 2b-v2 报告：宽障碍 + 新数据 + 双池对比 | 阶段 2b 验收通过（2026-07-08 holdout 一次性评估，项目所有者批准）： |
| 2026-07-08 | [`p2b_v3_barrier_sweep.md`](p2b_v3_barrier_sweep.md) | 2b-v3 探索：出场结构扫描（owner 2026-07-08 授意"试试止盈止损优化"） | 1. **TP5/SL2 是本轮最优出场**：唯一在 0.3% 成本下净收益明显为正的结构 |
| 2026-07-08 | [`p3_backtest_report.md`](p3_backtest_report.md) | 阶段 3 报告：事件驱动回测（第一轮） | 阶段 3 验收未通过**：验收窗口（157 笔）在基准成本 0.3% 下净收益 +0.06%（勉强为正）、 |
| 2026-07-07 | [`PROJECT_FULL_REPORT_20260728.md`](PROJECT_FULL_REPORT_20260728.md) | fable-trading 全程报告(2026-07-07 ~ 2026-07-28) | 训练池 5802 笔: |
| 2026-07-07 | [`p0_alpha_report.md`](p0_alpha_report.md) | P0 报告：人工标签是否含 alpha？ | 人工标签在"收益端"没有 alpha，但在"风险端"有真实且显著的 alpha**： |
| 2026-07-07 | [`p2a_detection_report.md`](p2a_detection_report.md) | P2a 报告：YOLO 检测双均线密集区域 | 检测层冒烟流水线已跑通：**val mAP50 = 0.835**（best.pt 官方评估），超过 0.8 冒烟验收线。 |
| 2026-07-07 | [`p2b_judgment_report.md`](p2b_judgment_report.md) | 阶段 2b 报告：判断层（triple-barrier + LightGBM） | 判断层信号统计上真实存在（holdout AUC 0.59，置换检验 p=0.002，稳定优于单特征基线）， |
| 2026-05-04 | [`strategy_stability_preholdout.md`](strategy_stability_preholdout.md) | Pre-holdout strategy stability audit |  |
| — | [`OPEN_QUESTIONS_FOR_RESEARCH.md`](OPEN_QUESTIONS_FOR_RESEARCH.md) | 卡点与待研究问题清单(给外部调研用) |  |
| — | [`ma206_profitability_diagnosis.md`](ma206_profitability_diagnosis.md) | MA206 收益为什么弱 |  |
| — | [`ma206_q80_shadow_diagnosis.md`](ma206_q80_shadow_diagnosis.md) | MA206 q80 影子漏斗诊断 | 当前不是“只监控 50 多个币”。本地共有 `401` 个 OKX USDT SWAP 15m 文件；按既定 |
| — | [`p1_owner_eth_perfect_platform_semantic_audit_20260811.md`](p1_owner_eth_perfect_platform_semantic_audit_20260811.md) | ETH 完美平台语义审查：短延迟、多位置、不自动贴标签 | 发现 \| 证据 \| 严重度 \| 置信度 \| 裁决 \| |
| — | [`p1_owner_gold_center_crop_review_20260811.md`](p1_owner_gold_center_crop_review_20260811.md) | P1 原始空头金标中心裁切审核 | 当前61张Codex逐图目测橙框不再作为下一版标签来源。新的审核包直接联结两份Owner事实： |
| — | [`p1_owner_short_gold_center_dataset_20260811.md`](p1_owner_short_gold_center_dataset_20260811.md) | P1 Owner空头金标中心裁切全量数据集 | Owner确认“不要Codex重新手割；从最早金标红框中心取几根K线作为橙框”后，已将该合同扩到完整Owner-short母池。 |
| — | [`p2_owner_short_hardneg_canary_owner_review_20260811.md`](p2_owner_short_hardneg_canary_owner_review_20260811.md) | Owner审核结论：当前模型约20%精确命中 | 331个事件已全部完成Owner裁决且数据可信。** 协议、源事件SHA、ID集合和声明计数全部一致， |
| — | [`p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.md`](p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.md) | P2 难负例扩充 V2 Owner 裁决报告 | Owner 已完成第二张 train-time 难负例扩充页 200/200 裁决：**25 个目标形态、0 个框偏、175 个难负例、0 pending**。协议、源 SHA、200 个唯一 ID、声明计数和 manifest 一一联结全部通过。 |
| — | [`p2_owner_short_train_hardneg_expansion200_v2_report_20260811.md`](p2_owner_short_train_hardneg_expansion200_v2_report_20260811.md) | P2 第三训练臂难负例扩充 200 张报告 | 45% 与 9% 只证明选样富集方向不同，不能证明模型总体 precision 是45%，也不能证明模型已经改善。 |
| — | [`p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.md`](p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.md) | P2 新训练时间块难负例扩挖 200 张报告 |  |
| — | [`p2_owner_short_train_hardneg_review200_report_20260811.md`](p2_owner_short_train_hardneg_review200_report_20260811.md) | P2 训练区间难负例候选 200 张 Owner 审核报告 |  |
| — | [`p2_owner_short_train_positive_retrieval100_report_20260811.md`](p2_owner_short_train_positive_retrieval100_report_20260811.md) | P2 第三训练臂前置：训练区间正例检索 100 张报告 | Owner 在新页按 `1=对 / 2=框偏 / 3=不对`，完成后复制 JSON。 |
| — | [`p2a_causal_direction_dataset_report.md`](p2a_causal_direction_dataset_report.md) | P2a 因果方向分类数据集验收 |  |
| — | [`p2a_causal_direction_profit_report.md`](p2a_causal_direction_profit_report.md) | P2a 因果方向 YOLO 经济性验收 | 固定 `yolo11n-cls` 因果方向分类器训练自然结束，epoch 6 为最佳，epoch 14 因 patience |
| — | [`p2a_e21b_sahi_report.md`](p2a_e21b_sahi_report.md) | P2a E2.1b 固定 SAHI 全验证基准 | 固定 SAHI 参数在 E2.1b 全部 1,255 张验证图上验收失败。Direct YOLO 精确复现既有 |
| — | [`p2a_golden_round1.md`](p2a_golden_round1.md) | 金标准 Round-1：owner vs 规则 分歧报告 |  |
| — | [`p2b_eth_micro_channel.md`](p2b_eth_micro_channel.md) | ETH Micro 通道（1/2/3/5m） |  |
| — | [`p2b_factor_ic_report.md`](p2b_factor_ic_report.md) | H19 外部 alpha 因子 IC 筛选（SWAP池, train/val, 未碰holdout） |  |
| — | [`p2b_ma206_comparison.md`](p2b_ma206_comparison.md) | P0-3 均线 20/60/120 对比实验（val only） | 1. **推荐：判断层主线暂时保持 8-55。** 理由是本项目成功标准看净收益， |
| — | [`p_prereg_holdout9_midvol.md`](p_prereg_holdout9_midvol.md) | 预注册卡 — holdout 第 9 次消耗:中波动带 × 高置信 |  |
| — | [`p_v14_windows_train.md`](p_v14_windows_train.md) | v14 pad200 → Windows（3060）训练交接 |  |
| — | [`short_tf_side_channel.md`](short_tf_side_channel.md) | 短周期支线（1m / 5m） |  |

## 按文件名(便于 grep)

- [`OPEN_QUESTIONS_FOR_RESEARCH.md`](OPEN_QUESTIONS_FOR_RESEARCH.md) — 卡点与待研究问题清单(给外部调研用)
- [`PROJECT_FULL_REPORT_20260728.md`](PROJECT_FULL_REPORT_20260728.md) — fable-trading 全程报告(2026-07-07 ~ 2026-07-28)
- [`STATE_20260730.md`](STATE_20260730.md) — 项目状态与交接 · 2026-07-30
- [`arch_overview_20260730.md`](arch_overview_20260730.md) — fable-trading 架构与现状总览（2026-07-30）
- [`backlog_future_optimizations.md`](backlog_future_optimizations.md) — 未来优化 backlog（现在不做）
- [`eth3m_short_pilot_v2_cls_maintenance_plan.md`](eth3m_short_pilot_v2_cls_maintenance_plan.md) — ETH 3m v2 分类诊断脚本维护例外
- [`eth3m_short_pilot_v2a_maintenance_plan.md`](eth3m_short_pilot_v2a_maintenance_plan.md) — ETH 3m pilot v2a 大脚本维护例外与拆分计划
- [`evening_checklist_20260730.md`](evening_checklist_20260730.md) — 本晚问题梳理与处理清单（2026-07-30 → 07-31）
- [`forward_mainline_status_20260720.md`](forward_mainline_status_20260720.md) — 前向 / 主线诚实状态摘要（2026-07-20）
- [`h_tip_plan.md`](h_tip_plan.md) — H-TIP — tip-firing for live YOLO
- [`lightgbm_system_and_tooling_review.md`](lightgbm_system_and_tooling_review.md) — LightGBM 判断层与工具接入评估
- [`local_signal_v2_progress.md`](local_signal_v2_progress.md) — Local Signal V2 — 进度一页纸
- [`ma206_profitability_diagnosis.md`](ma206_profitability_diagnosis.md) — MA206 收益为什么弱
- [`ma206_q80_shadow_24h_report.md`](ma206_q80_shadow_24h_report.md) — MA206 q80 影子 24 小时终验
- [`ma206_q80_shadow_diagnosis.md`](ma206_q80_shadow_diagnosis.md) — MA206 q80 影子漏斗诊断
- [`night_report_20260721.md`](night_report_20260721.md) — 晨报 / 批次状态（2026-07-21）
- [`oss_architecture_benchmark.md`](oss_architecture_benchmark.md) — 开源架构基准与隔离试点
- [`p0_alpha_report.md`](p0_alpha_report.md) — P0 报告：人工标签是否含 alpha？
- [`p0_baseline_audit_20260803.md`](p0_baseline_audit_20260803.md) — P0.0 基线审计 —— 仓库现状 vs Grok Build 接管计划
- [`p0_independent_acceptance_20260803.md`](p0_independent_acceptance_20260803.md) — P0 独立验收报告（2026-08-03）
- [`p0_local_signal_v2_audit_20260807.md`](p0_local_signal_v2_audit_20260807.md) — P0 — 局部信号 V2 交接规范：旧管线审计、基线冻结与因果门测量
- [`p0_local_signal_v2_stagea_randomcrop_v1_report_20260811.md`](p0_local_signal_v2_stagea_randomcrop_v1_report_20260811.md) — Local Signal V2 Stage A 真实裁剪 P0 报告（2026-08-11）
- [`p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md`](p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md) — Local Signal V2 Stage B-from-A 数据验收报告（2026-08-11）
- [`p0_local_signal_v2_stageb_report.md`](p0_local_signal_v2_stageb_report.md) — P0 — Local Signal V2 Stage B：因果数据集重建与硬门槛通过
- [`p0_local_signal_v2_stageb_strictneg_v2_report.md`](p0_local_signal_v2_stageb_strictneg_v2_report.md) — P0 修复 — Local Signal V2 Stage B strict-negative V2
- [`p0_runtime_parity_audit_20260803.md`](p0_runtime_parity_audit_20260803.md) — P0 Runtime Parity 审计（2026-08-03）
- [`p0_safety_protocol_repair_20260803.md`](p0_safety_protocol_repair_20260803.md) — P0-SAFETY short 协议修复报告（2026-08-03）
- [`p15_h10_short_report.md`](p15_h10_short_report.md) — P1.5 R2：H10 做空侧镜像验证
- [`p15_h1_h2_exit_report.md`](p15_h1_h2_exit_report.md) — P1.5 R3：H1/H2 出场复合验证
- [`p15_h3_ma_exit.md`](p15_h3_ma_exit.md) — P1.5 H3：结构出场（收盘跌破 EMA21）
- [`p15_h4_time_decay.md`](p15_h4_time_decay.md) — P1.5 H4：时间衰减紧缩出场
- [`p15_h5_vol_adaptive.md`](p15_h5_vol_adaptive.md) — P1.5 H5：波动率自适应障碍
- [`p15_h9_report.md`](p15_h9_report.md) — P1.5 R1'：H9 高层趋势过滤复测与推广
- [`p1_b2_short_l2_backtest_20260811.md`](p1_b2_short_l2_backtest_20260811.md) — Local Signal V2 B2：候选密度与收益诊断
- [`p1_local_signal_v2_position_shortcut_20260811.md`](p1_local_signal_v2_position_shortcut_20260811.md) — Local Signal V2 位置 shortcut 纠错（2026-08-11）
- [`p1_local_signal_v2_prereg_20260810.md`](p1_local_signal_v2_prereg_20260810.md) — P1 局部因果窗口对照预注册
- [`p1_local_signal_v2_report_20260811.md`](p1_local_signal_v2_report_20260811.md) — Local Signal V2 P1 局部因果窗口对照报告
- [`p1_local_signal_v2_stagea_gap_to_owner_target_20260811.md`](p1_local_signal_v2_stagea_gap_to_owner_target_20260811.md) — Local Signal V2：昨晚 3060 Stage A 与 Owner 最终目标差距复盘
- [`p1_local_signal_v2_stagea_position_eval_20260811.md`](p1_local_signal_v2_stagea_position_eval_20260811.md) — Local Signal V2 Stage A 训练与分位置诊断（2026-08-11）
- [`p1_local_signal_v2_stageb_cold_report.md`](p1_local_signal_v2_stageb_cold_report.md) — P1 — Local Signal V2 Stage B 冷启动（owner_lsv2_stageb_cold）
- [`p1_owner_eth_perfect_platform_semantic_audit_20260811.md`](p1_owner_eth_perfect_platform_semantic_audit_20260811.md) — ETH 完美平台语义审查：短延迟、多位置、不自动贴标签
- [`p1_owner_eth_shortdelay_boundary_contract_20260811.md`](p1_owner_eth_shortdelay_boundary_contract_20260811.md) — ETH完美平台：竖线内核心与3–5根短延迟合同
- [`p1_owner_eth_shortdelay_calibration30_20260811.md`](p1_owner_eth_shortdelay_calibration30_20260811.md) — P1 Owner ETH 短延迟动态窗口 30 张校准报告（2026-08-11）
- [`p1_owner_eth_shortdelay_codex_firstpass_20260811.md`](p1_owner_eth_shortdelay_codex_firstpass_20260811.md) — P1 Owner ETH 短延迟语义 Codex 一审（2026-08-11）
- [`p1_owner_eth_shortdelay_dynamic_review200_20260811.md`](p1_owner_eth_shortdelay_dynamic_review200_20260811.md) — P1 Owner ETH 空头动态短窗 200 张扩展、一审与逐图改框（2026-08-11）
- [`p1_owner_gold_center_crop_review_20260811.md`](p1_owner_gold_center_crop_review_20260811.md) — P1 原始空头金标中心裁切审核
- [`p1_owner_short_gold_center_dataset_20260811.md`](p1_owner_short_gold_center_dataset_20260811.md) — P1 Owner空头金标中心裁切全量数据集
- [`p1_owner_short_gold_center_recent2d_holdout_20260811.md`](p1_owner_short_gold_center_recent2d_holdout_20260811.md) — Owner-short compact YOLO 最近2天全市场回放（2026-08-11）
- [`p1_preholdout_dataset_rebuild_20260803.md`](p1_preholdout_dataset_rebuild_20260803.md) — P1-DATA：pre-holdout immutable short L2 dataset 重建验收
- [`p25_daily_workflow_acceptance_20260710.md`](p25_daily_workflow_acceptance_20260710.md) — MA206 每日安全链验收（2026-07-10）
- [`p25_local_acceptance_20260710.md`](p25_local_acceptance_20260710.md) — P2.5 本地验收（2026-07-10）
- [`p25_vps_acceptance_20260710.md`](p25_vps_acceptance_20260710.md) — P2.5 VPS 公网验收（2026-07-10）
- [`p2_data_audit_report.md`](p2_data_audit_report.md) — P2-12 数据质量审计
- [`p2_l2_audit_and_prereg_20260803.md`](p2_l2_audit_and_prereg_20260803.md) — P2-L2 只读审计与预注册（训练前 Owner 门）
- [`p2_l2_preholdout_validation_20260803.md`](p2_l2_preholdout_validation_20260803.md) — P2-L2：immutable P1 dataset 训练与 pre-holdout 验收
- [`p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md`](p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md) — Local Signal V2 Positive 语义纯度审计 PRE-REVIEW（2026-08-12）
- [`p2_owner_short_gold_center_hardneg_arm_20260811.md`](p2_owner_short_gold_center_hardneg_arm_20260811.md) — P2 Owner-short compact YOLO Hard-Negative第二训练臂（2026-08-11）
- [`p2_owner_short_gold_center_hardneg_canary_20260811.md`](p2_owner_short_gold_center_hardneg_canary_20260811.md) — P2 Owner-short Hard-Negative重训与连续密度Canary（2026-08-11）
- [`p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md`](p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md) — P2 Owner-short Hard-Negative Canary 331事件审核包（2026-08-11）
- [`p2_owner_short_gold_center_hardneg_r2_canary_20260812.md`](p2_owner_short_gold_center_hardneg_r2_canary_20260812.md) — P2 Owner确认误报第三训练臂与独立连续Canary（2026-08-12）
- [`p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md`](p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md) — P2 Owner确认误报第三训练臂数据审计（2026-08-11）
- [`p2_owner_short_hardneg_canary_owner_review_20260811.md`](p2_owner_short_hardneg_canary_owner_review_20260811.md) — Owner审核结论：当前模型约20%精确命中
- [`p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.md`](p2_owner_short_train_hardneg_expansion200_v2_owner_review_20260811.md) — P2 难负例扩充 V2 Owner 裁决报告
- [`p2_owner_short_train_hardneg_expansion200_v2_report_20260811.md`](p2_owner_short_train_hardneg_expansion200_v2_report_20260811.md) — P2 第三训练臂难负例扩充 200 张报告
- [`p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.md`](p2_owner_short_train_hardneg_newblocks200_v3_report_20260811.md) — P2 新训练时间块难负例扩挖 200 张报告
- [`p2_owner_short_train_hardneg_review200_report_20260811.md`](p2_owner_short_train_hardneg_review200_report_20260811.md) — P2 训练区间难负例候选 200 张 Owner 审核报告
- [`p2_owner_short_train_positive_retrieval100_report_20260811.md`](p2_owner_short_train_positive_retrieval100_report_20260811.md) — P2 第三训练臂前置：训练区间正例检索 100 张报告
- [`p2a_ab_leak_correction.md`](p2a_ab_leak_correction.md) — A/B 泄漏更正与干净检验（2026-07-15）
- [`p2a_bad_images_pack.md`](p2a_bad_images_pack.md) — P2-11 偏 B · 坏图清单（Round 1 → E2）
- [`p2a_causal_direction_dataset_report.md`](p2a_causal_direction_dataset_report.md) — P2a 因果方向分类数据集验收
- [`p2a_causal_direction_profit_report.md`](p2a_causal_direction_profit_report.md) — P2a 因果方向 YOLO 经济性验收
- [`p2a_consistency_e21_vs_old_best.md`](p2a_consistency_e21_vs_old_best.md) — Consistency: E2.1 GT vs old yolo11s best.pt preds
- [`p2a_detection_report.md`](p2a_detection_report.md) — P2a 报告：YOLO 检测双均线密集区域
- [`p2a_e1_xpad_report.md`](p2a_e1_xpad_report.md) — P2-11 E1 — 收紧 `x_pad_px`（12 → 6）
- [`p2a_e21_train_interim.md`](p2a_e21_train_interim.md) — YOLO E2.1 training interim (train EXITED)
- [`p2a_e21_train_report.md`](p2a_e21_train_report.md) — P2a YOLO E2.1 formal retrain report
- [`p2a_e21b_hsv0_report.md`](p2a_e21b_hsv0_report.md) — P2a E2.1b 全 HSV 关闭正式验收
- [`p2a_e21b_sahi_report.md`](p2a_e21b_sahi_report.md) — P2a E2.1b 固定 SAHI 全验证基准
- [`p2a_e2_max_dense_report.md`](p2a_e2_max_dense_report.md) — P2-11 E2 — 长段收核 `MAX_DENSE_BARS=24`
- [`p2a_golden_round1.md`](p2a_golden_round1.md) — 金标准 Round-1：owner vs 规则 分歧报告
- [`p2a_hts_report.md`](p2a_hts_report.md) — H-TS — 检测层训练图时间切分实验
- [`p2a_label_audit_round1.md`](p2a_label_audit_round1.md) — P2-11 YOLO Label Audit Round 1
- [`p2a_lr_bug_audit.md`](p2a_lr_bug_audit.md) — p2a — 学习率 bug 审计与 v8 重训
- [`p2a_v12_mainline_cutover.md`](p2a_v12_mainline_cutover.md) — 检测主线切 v12（owner 强制）— 2026-07-20
- [`p2a_yolo_critical_path_ab.md`](p2a_yolo_critical_path_ab.md) — A/B: YOLO候选源 vs 规则候选源（SWAP，发现级 val-only）
- [`p2a_yolo_mainline_cutover.md`](p2a_yolo_mainline_cutover.md) — YOLO 主线切换（owner 2026-07-15）
- [`p2b_eth_micro_channel.md`](p2b_eth_micro_channel.md) — ETH Micro 通道（1/2/3/5m）
- [`p2b_factor_ic_report.md`](p2b_factor_ic_report.md) — H19 外部 alpha 因子 IC 筛选（SWAP池, train/val, 未碰holdout）
- [`p2b_factor_ic_vol.md`](p2b_factor_ic_vol.md) — H14/H17/H18 成交量因子三连 IC 筛选（SWAP 池, train/val）
- [`p2b_h11_tiered.md`](p2b_h11_tiered.md) — H11 市值/流动性分层模型（SWAP 24h 成交额中位数二分）
- [`p2b_h13_btc_regime.md`](p2b_h13_btc_regime.md) — H13 BTC 大盘状态共享特征（SWAP 池, train/val）
- [`p2b_h15_quality.md`](p2b_h15_quality.md) — H15 密集质量二阶特征 IC 筛选（SWAP 池, train/val）
- [`p2b_h8_30m_grid.md`](p2b_h8_30m_grid.md) — H8 后续：30m 网格 TP{4,5,6}×horizon{48,60,72}
- [`p2b_hf_2m_3m_data_feasibility.md`](p2b_hf_2m_3m_data_feasibility.md) — 2m / 3m 高频影子数据可行性
- [`p2b_judgment_audit.md`](p2b_judgment_audit.md) — p2b — 判断层全面体检 + 两个前沿改造实验(J-1/J-2)
- [`p2b_judgment_report.md`](p2b_judgment_report.md) — 阶段 2b 报告：判断层（triple-barrier + LightGBM）
- [`p2b_low_tf_backtest_report.md`](p2b_low_tf_backtest_report.md) — 低周期回测：1m / 2m / 3m / 5m vs 15m
- [`p2b_ma206_comparison.md`](p2b_ma206_comparison.md) — P0-3 均线 20/60/120 对比实验（val only）
- [`p2b_ma206_mainline_migration.md`](p2b_ma206_mainline_migration.md) — P2b 判断层统一 SMA/EMA 20/60/120
- [`p2b_ml_layer_opt_summary.md`](p2b_ml_layer_opt_summary.md) — ML 层可优化方向 — 实测扫描总结
- [`p2b_ml_opt_rules_expanded_report.md`](p2b_ml_opt_rules_expanded_report.md) — ML 层优化扫描（YOLO 判断池，val-only）
- [`p2b_ml_opt_swap_tp5_report.md`](p2b_ml_opt_swap_tp5_report.md) — ML 层优化扫描（YOLO 判断池，val-only）
- [`p2b_ml_opt_yolo_report.md`](p2b_ml_opt_yolo_report.md) — ML 层优化扫描（YOLO 判断池，val-only）
- [`p2b_mtf_report.md`](p2b_mtf_report.md) — P1.5 R4：H7/H8 多时间框架池
- [`p2b_v2_report.md`](p2b_v2_report.md) — 阶段 2b-v2 报告：宽障碍 + 新数据 + 双池对比
- [`p2b_v3_barrier_sweep.md`](p2b_v3_barrier_sweep.md) — 2b-v3 探索：出场结构扫描（owner 2026-07-08 授意"试试止盈止损优化"）
- [`p2b_yolo_reg_active_cutover.md`](p2b_yolo_reg_active_cutover.md) — 判断层切 ACTIVE：YOLO + 回归 realized_ret
- [`p2m_readonly_mechanism_audit_20260803.md`](p2m_readonly_mechanism_audit_20260803.md) — P2-M：ATR 尺度与形态关联的只读机制审计
- [`p2r_readonly_root_cause_audit_20260803.md`](p2r_readonly_root_cause_audit_20260803.md) — P2-R：P1 immutable 上的只读根因审计
- [`p3_backtest_report.md`](p3_backtest_report.md) — 阶段 3 报告：事件驱动回测（第一轮）
- [`p3_ml_opt_backtest_compare.md`](p3_ml_opt_backtest_compare.md) — 回测对照：二分类 vs 回归收益（YOLO 主线池）
- [`p3_v11_pool_cutover.md`](p3_v11_pool_cutover.md) — p3 — v11 池判断层切换 ACTIVE
- [`p3_v8_pool_cutover.md`](p3_v8_pool_cutover.md) — p3 — 干净池(v8_chain)判断层切换 ACTIVE
- [`p3_yolo_mainline_backtest.md`](p3_yolo_mainline_backtest.md) — YOLO 主线整体回测（切流后，2026-07-15）
- [`p_20260728_four_tracks.md`](p_20260728_four_tracks.md) — 2026-07-28 四件事的结果 + 判断层判定
- [`p_20260728_matched_control_verdict.md`](p_20260728_matched_control_verdict.md) — 对照组终判：检测器的边 ≈ 成本，而金标本身没有一个盘口样本 — 2026-07-28
- [`p_attribution_23bp_vs_minus16bp_20260803.md`](p_attribution_23bp_vs_minus16bp_20260803.md) — 归因:+23.49bp 与 -15.91bp 的 44bp 差从哪来
- [`p_base_rate_dense_verdict.md`](p_base_rate_dense_verdict.md) — 密集几何 base rate 终判:信号真实但边际,成本才是杀手 — 2026-07-23
- [`p_box_to_bar_lag.md`](p_box_to_bar_lag.md) — 框→bar 滞后机制（EDEN / KORU）— 2026-07-21
- [`p_chain_failure_attribution.md`](p_chain_failure_attribution.md) — 密集链路失败归因 — 哪一层是主因 — 2026-07-23
- [`p_chartscanai_review.md`](p_chartscanai_review.md) — ChartScanAI 详细评测 — 对 fable-trading 有什么用
- [`p_direction_select_base_rate.md`](p_direction_select_base_rate.md) — 因果择向 base rate — 2026-07-23
- [`p_e3_sparse_and_two_stage.md`](p_e3_sparse_and_two_stage.md) — E3 稀疏化 + 两段式确认 — 2026-07-23
- [`p_entry_align_and_regime.md`](p_entry_align_and_regime.md) — E1 入场对齐 owner short + E2 regime 门 — 2026-07-23
- [`p_entry_timing_close_vs_next.md`](p_entry_timing_close_vs_next.md) — 入场时机：signal_close vs next_open — 2026-07-23
- [`p_eth3m_short_pilot_v2_cls_diag_20260730.md`](p_eth3m_short_pilot_v2_cls_diag_20260730.md) — ETH 3m short-start v2 图像分类诊断训练报告
- [`p_eth_3m_calibration240_preview.md`](p_eth_3m_calibration240_preview.md) — ETH 3m 双视图 240 张校准包预览
- [`p_eth_3m_entry_timing_calibration30.md`](p_eth_3m_entry_timing_calibration30.md) — ETH 3m 提前入场线 30 张校准包
- [`p_eth_3m_short_pilot_v1.md`](p_eth_3m_short_pilot_v1.md) — ETH 3m 做空检测器 pilot v1 — 数据质量与训练启动记录
- [`p_eth_3m_short_pilot_v1_backtest.md`](p_eth_3m_short_pilot_v1_backtest.md) — ETH 3m 专用做空模型 pilot v1 — 因果回放报告
- [`p_eth_3m_short_pilot_v2_dataset.md`](p_eth_3m_short_pilot_v2_dataset.md) — ETH 3m short-start pilot v2 数据集审计
- [`p_eth_3m_v10_owner_labels_timing.md`](p_eth_3m_v10_owner_labels_timing.md) — ETH 3m v10 owner 标注后的迟到诊断
- [`p_eth_3m_v10_prebox200.md`](p_eth_3m_v10_prebox200.md) — ETH 3m · v10 有框预标 200 张
- [`p_eth_3m_v10_prelabels_3m.md`](p_eth_3m_v10_prelabels_3m.md) — ETH 3m × v10 最近三个月预打标预览
- [`p_execution_slippage.md`](p_execution_slippage.md) — 执行折扣 / 滑点实测（2026-07-21）
- [`p_exit_parity.md`](p_exit_parity.md) — P-EXIT-PARITY：回测 vs 前向出场逻辑等价性验证（2026-07-20）
- [`p_forward_hindsight_20260719.md`](p_forward_hindsight_20260719.md) — 前向事后检出日结 — 2026-07-19
- [`p_frontend_viz_opt.md`](p_frontend_viz_opt.md) — 前端可视化优化 — 真落地 + 风格收敛
- [`p_github_optimize_candidates.md`](p_github_optimize_candidates.md) — GitHub 开源候选 — 对本仓真实痛点的第二轮筛选
- [`p_gpt_architecture_review_20260731.md`](p_gpt_architecture_review_20260731.md) — fable-trading 架构与方法学审阅（2026-07-31）
- [`p_how_to_unlock_label_to_trade_chain.md`](p_how_to_unlock_label_to_trade_chain.md) — 如何打通「打标 → 特征/因子 → 可交易」— 2026-07-24
- [`p_it14_visual_direction_precheck.md`](p_it14_visual_direction_precheck.md) — IT-14 · tip 窗图像素是否携带方向信号（冻结 COCO embed 预检）
- [`p_it15_tip_remap.md`](p_it15_tip_remap.md) — IT-15 · tip remap（框右缘 → 局部密度谷）— 诊断有用，不可当部署边
- [`p_judgment_layer_lab.md`](p_judgment_layer_lab.md) — 判断层重构实验室(活文档,持续迭代)— 起于 2026-07-24
- [`p_judgment_maker_cost_on_regtop.md`](p_judgment_maker_cost_on_regtop.md) — 选项 A 执行：回归 top 子集上的 maker 成本压降实测
- [`p_judgment_maker_trial_a2_plan.md`](p_judgment_maker_trial_a2_plan.md) — A2 实施计划：隔离 maker 试错桶（VPS 小仓验证）
- [`p_judgment_reg_whitebox.md`](p_judgment_reg_whitebox.md) — 回归预测 net + 白盒规则（推荐 1+3 验证）
- [`p_judgment_topdecile_profile_v10.md`](p_judgment_topdecile_profile_v10.md) — 剖开顶十分位：v10 池判断层 top-decile 特征画像与匹配对照
- [`p_judgment_topdecile_target_ab.md`](p_judgment_topdecile_target_ab.md) — A+B 实验：把「顶十分位」本身作为判断层新目标
- [`p_l2_v10_reg_freeze_20260731.md`](p_l2_v10_reg_freeze_20260731.md) — L2 切 v10 池回归 · 冻结与回测分析报告（2026-07-31）
- [`p_latest_code_review_20260723.md`](p_latest_code_review_20260723.md) — 最新代码审查 — 2026-07-23
- [`p_launch_entry_base_rate.md`](p_launch_entry_base_rate.md) — 启动入场 vs 盘整中入场：因果 base rate 单变量对照 — 2026-07-23
- [`p_launch_entry_long_short.md`](p_launch_entry_long_short.md) — 启动入场：强制多空分边 base rate — 2026-07-23
- [`p_live_readiness_checklist.md`](p_live_readiness_checklist.md) — 可上实盘检查清单（判断层重构 — 停在 Owner 点头门前）
- [`p_mtf_yolo_l2_bridge_prep_20260804.md`](p_mtf_yolo_l2_bridge_prep_20260804.md) — 小周期 YOLO → 冻结 L2 因果桥准备报告 — 2026-08-04
- [`p_overnight_20260722.md`](p_overnight_20260722.md) — 夜间工作纪要 — 2026-07-22
- [`p_owner_label_feature_verdict.md`](p_owner_label_feature_verdict.md) — Owner 标框手法 → 因果特征 → train base rate 裁决 — 2026-07-23
- [`p_owner_side_feature_verdict.md`](p_owner_side_feature_verdict.md) — Owner 分边标框 → 因果特征 → train base rate 裁决 — 2026-07-23
- [`p_owner_side_rich_features_verdict.md`](p_owner_side_rich_features_verdict.md) — Owner 扩特征分边裁决 — 2026-07-23
- [`p_owner_side_short_tip_v1b.md`](p_owner_side_short_tip_v1b.md) — owner_side_short_tip_v1b — tip-smoke 诚实评估（不 promote）
- [`p_pad200_cut_audit.md`](p_pad200_cut_audit.md) — pad200 切割审计 — Owner「框不对」— 2026-07-22
- [`p_pad200_regression_why.md`](p_pad200_regression_why.md) — 为什么「昨天修过 stem」v13 还是错窗 — 2026-07-22
- [`p_prereg_holdout9_midvol.md`](p_prereg_holdout9_midvol.md) — 预注册卡 — holdout 第 9 次消耗:中波动带 × 高置信
- [`p_project_overview_20260722.md`](p_project_overview_20260722.md) — 项目总览（给 Owner）— 2026-07-22 夜
- [`p_real_tip_collect_started.md`](p_real_tip_collect_started.md) — 真实 tip 成败金标小样 — 已开干（2026-07-22 夜）
- [`p_realtime_yolo_within_bar.md`](p_realtime_yolo_within_bar.md) — YOLO「bar 内实时推理」路线图 — 2026-07-21
- [`p_samesource_judgment_verdict.md`](p_samesource_judgment_verdict.md) — 同源判断层 + 新特征:walk-forward 证伪"稳健 edge" — 2026-07-23 夜
- [`p_short_judgment_100_6m_reg.md`](p_short_judgment_100_6m_reg.md) — short 100×6m 回归单切（发现级，未 holdout / 未 promote）
- [`p_short_judgment_100_6m_reg_walkforward.md`](p_short_judgment_100_6m_reg_walkforward.md) — short 100×6m 回归 — 5-fold walkforward（发现级，未 holdout）
- [`p_short_judgment_30_6m_reg_walkforward.md`](p_short_judgment_30_6m_reg_walkforward.md) — short 30×6m 回归 — 5-fold walkforward（发现级，未 holdout）
- [`p_short_judgment_refactor_v1.md`](p_short_judgment_refactor_v1.md) — Short 判断层重构 v1：结构性 short-only 路径 + 特征方向镜像单变量实验
- [`p_short_judgment_refactor_v2.md`](p_short_judgment_refactor_v2.md) — Short 判断层重构 v2：扩币（30×6m）镜像基线 + top-K 单变量
- [`p_short_judgment_reg_align_v11.md`](p_short_judgment_reg_align_v11.md) — 纠偏：short 判断层对齐 v11 回归主链
- [`p_short_only_backtest_tip_v1b_5_6m.md`](p_short_only_backtest_tip_v1b_5_6m.md) — SHORT 回测：tip_v1b × 5 流动性币 × 6m（pre-holdout）
- [`p_short_only_pipeline.md`](p_short_only_pipeline.md) — 只做空全链路作战计划（short-only pipeline）
- [`p_short_tip_v1b_detect1000.md`](p_short_tip_v1b_detect1000.md) — tip_v1b 实际 K 线 ~1000 框包（S3，不 promote）
- [`p_short_tip_v1b_detect1000_shortish.md`](p_short_tip_v1b_detect1000_shortish.md) — tip_v1b 1000 框 → 空头观感过滤包（S3 补丁，不 promote）
- [`p_short_trend_ab.md`](p_short_trend_ab.md) — 空边趋势出场 A/B — 稳健性 + owner short 对照 — 2026-07-23
- [`p_short_trend_holdout7.md`](p_short_trend_holdout7.md) — Holdout #7 — A 因果空边趋势出（no_tp / trail4）— 2026-07-23
- [`p_side_tools_landed.md`](p_side_tools_landed.md) — 本机旁路工具集落地 — 发现级收尾
- [`p_tip_eval_fairness.md`](p_tip_eval_fairness.md) — tip 验收公平性审计 — tip-smoke / tip_hit 会不会冤假错案？
- [`p_tip_mapping_owner_intent.md`](p_tip_mapping_owner_intent.md) — tip 映射审计：`box_right_frac≈0.5` 是否冤枉 Owner「框=tip」
- [`p_tip_only_smoke.md`](p_tip_only_smoke.md) — tip-only 扫描冒烟诊断 — 2026-07-21
- [`p_tip_subset_val.md`](p_tip_subset_val.md) — p_tip_subset_val — tip 可检子集 vs 全量基线（严格 val 窗）
- [`p_trend_exit_base_rate.md`](p_trend_exit_base_rate.md) — 趋势出场 base rate — 2026-07-23
- [`p_v12_htip_eval.md`](p_v12_htip_eval.md) — H-TIP v12 评测（D1）— 2026-07-20
- [`p_v12_score_shift.md`](p_v12_score_shift.md) — 路 C：检测 v12 × 判断 v11 冻结 —— val 窗小段重扫分数漂移测量
- [`p_v12_shadow_start.md`](p_v12_shadow_start.md) — v12 影子启动记录 — 2026-07-20
- [`p_v13_pad200_train.md`](p_v13_pad200_train.md) — v13 pad200 终局 + H-DET-1 tip 对照 — 2026-07-22
- [`p_v13_real_tip_collect_plan.md`](p_v13_real_tip_collect_plan.md) — v13 — 收集 live 真实 tip 成败图（计划）
- [`p_v13_why_bad_train.md`](p_v13_why_bad_train.md) — 为什么 v13 训这么差？训练集诊断 — 2026-07-22
- [`p_v14_failure_rootcause.md`](p_v14_failure_rootcause.md) — v14 tip 仍失败 — 根因分析（有证据）— 2026-07-22
- [`p_v14_pad200_rebuild.md`](p_v14_pad200_rebuild.md) — v14 pad200 重建（MAD-on）— 2026-07-22
- [`p_v14_pad200_train.md`](p_v14_pad200_train.md) — v14 pad200（MAD-on）终局 + tip 对照 — 2026-07-22
- [`p_v14_sample30.md`](p_v14_sample30.md) — v14 pad200 抽检 30 张 + okx 错窗小样 — 2026-07-22
- [`p_v14_windows_train.md`](p_v14_windows_train.md) — v14 pad200 → Windows（3060）训练交接
- [`p_v15_dataset_confound.md`](p_v15_dataset_confound.md) — v15 败因定论:正负样本来自两条渲染管线(风格捷径)— 2026-07-23
- [`p_v15_revalidate_fair.md`](p_v15_revalidate_fair.md) — v15 发现级公平重验 — 2026-07-23
- [`p_v15_tip_val.md`](p_v15_tip_val.md) — v15 tip-val（Hypothesis B）中期裁决 — 2026-07-23
- [`p_v16_holdout_verdict.md`](p_v16_holdout_verdict.md) — v16 holdout 终审:纯检测亏损,判断层反预测 — 2026-07-23
- [`p_v16_tipuni_train.md`](p_v16_tipuni_train.md) — v16 tipuni(统一管线冷启动)训练与金标验收 — 2026-07-23
- [`p_w20_manifest_traceability_20260810.md`](p_w20_manifest_traceability_20260810.md) — w20 / lsv2 数据集可追溯性与可复现性审计 — 2026-08-10
- [`p_w20_midbox_tip_backtest_20260807.md`](p_w20_midbox_tip_backtest_20260807.md) — w20 midbox tip 回测裁决 — 2026-08-07
- [`p_weight_centric_val.md`](p_weight_centric_val.md) — p_weight_centric — score→size 连续仓位 vs 二元 all-in（严格 val 窗离线回测）
- [`p_window_200_rationale.md`](p_window_200_rationale.md) — 检测窗为什么是 200 根 K 线？合理吗？如何提高检出准确度
- [`p_wuzao_a_tier_done.md`](p_wuzao_a_tier_done.md) — wuzao A 档落地短报（2026-07-22 夜）
- [`p_wuzao_more_useful.md`](p_wuzao_more_useful.md) — 无噪 topics：前端之外还有哪些对本仓真正好用
- [`p_wuzao_topics_scan.md`](p_wuzao_topics_scan.md) — 无噪（wuzao）全站 topics 扫描 — 对本仓可迁移性
- [`p_yolo_dense_hypotheses.md`](p_yolo_dense_hypotheses.md) — YOLO 均线密集检测层假设簇（H-DET）— 发现级汇总
- [`p_yolo_external_sources.md`](p_yolo_external_sources.md) — 外源调研：YOLO「均线密集 / 盘口 tip」可迁移点子
- [`p_yolo_while_v13_trains.md`](p_yolo_while_v13_trains.md) — v13 训练期间可做项 — 短报告（2026-07-22）
- [`prereg_attribution_20260803.md`](prereg_attribution_20260803.md) — 预注册:+23.49bp 与 -15.91bp 的归因
- [`project_management_plan_20260724.md`](project_management_plan_20260724.md) — fable-trading 项目管理计划（2026-07-24）
- [`shadow_booster_framework_comparison.md`](shadow_booster_framework_comparison.md) — LightGBM / CatBoost / XGBoost / Ensemble 影子比较
- [`short_tf_side_channel.md`](short_tf_side_channel.md) — 短周期支线（1m / 5m）
- [`strategy_stability_preholdout.md`](strategy_stability_preholdout.md) — Pre-holdout strategy stability audit
- [`todo_short_only_pipeline.md`](todo_short_only_pipeline.md) — Short-only 链路待办
- [`two_day_final_audit_20260711.md`](two_day_final_audit_20260711.md) — 两日任务最终审计（2026-07-11）
- [`two_day_pre_final_audit_20260710.md`](two_day_pre_final_audit_20260710.md) — 两日任务预终审（2026-07-10）
- [`week_plan_20260720.md`](week_plan_20260720.md) — 一周执行计划(2026-07-20 → 07-27)— 交给 Grok 执行版
- [`week_plan_20260803.md`](week_plan_20260803.md) — 一周执行计划（2026-08-03 → 08-09）
