# analysis/ 报告索引（自动生成,勿手改）

共 **576** 篇。重跑刷新:`PYTHONPATH=. .venv/bin/python scripts/gen_analysis_index.py`

> **动手前先在这里搜一遍**——这个索引存在的原因是:曾经差点重跑 owner 已标完的 2525 个
> 多空框(`p_owner_side_feature_verdict.md` 早有结论),也曾两个会话各自做了一遍同样的
> 视觉方向预检。**结论列是原文摘录,不是我的转述;空 = 机器提不出,不是没结论——去读原文。**

## 按日期倒序

| 日期 | 报告 | 标题 | 结论(原文摘录) |
|---|---|---|---|
| 2026-09-20 | [`p1_spike_v112_entry_extension_20260920.md`](p1_spike_v112_entry_extension_20260920.md) | V11.2：限制入场前价格推进，三个周期均未通过研究筛查 | 结论：固定29币，只试一个因果入场门——突破收盘相对父V9收盘的推进，不超过父信号收盘到原止损的一份距离。15m和1h全期净R从负转正，但不足以接受此规则：三个周期的全期改善区间都跨0，1h后段仍亏，4h后段更差。保留15m时机线索；拒绝本规则准入，不搜索新阈值，不修改Pine/监控/实盘。 |
| 2026-09-20 | [`p1_spike_v112_one_line_audit_20260920.md`](p1_spike_v112_one_line_audit_20260920.md) | V11.2：ONE 15m白色下降线卡在第三个独立高点 | 结论：按当前默认线性/双轨规则和本地同段OKX数据，这条图示线首先卡在建线。第三次贴线虽很准，但不被12左/8右的pivot定义承认为独立高点，且相邻间隔只有7根，低于24根。另一个独立问题是1h已出现突破时，15m没有开放V9多头参考框，因此不产生框内联合。未改脚本或参数。 |
| 2026-09-20 | [`p1_spike_v112_trade_review_20260920.md`](p1_spike_v112_trade_review_20260920.md) | V11.2：29币570笔逐笔复盘与8张行情图 | 结论：570次入场全部重放并生成逐笔说明，568笔已平仓、187盈、381亏，2笔边界仍持仓。381笔亏损里127笔曾到1R、33笔曾到2R；“先有明显浮盈再回吐”真实存在，但不能解释多数亏单。最大的亏损类别是未到1R便价格止损，共234笔。8张图按机制选取，另有29份逐币明细和完整中文CSV。 |
| 2026-09-20 | [`p1_spike_v12_local_touch_20260920.md`](p1_spike_v12_local_touch_20260920.md) | SPIKE V12：主高点＋局部回踩确认 |  |
| 2026-09-19 | [`p1_spike_v112_1h_diagnostics_20260919.md`](p1_spike_v112_1h_diagnostics_20260919.md) | SPIKE V11.2 1h：完整回测数据与失败路径分析 |  |
| 2026-09-19 | [`p1_spike_v112_audit_20260919.md`](p1_spike_v112_audit_20260919.md) | SPIKE V11.2「框内突破+spike」审查：已有负面答案，剩余问题是交易口径、一致性与前向证据 |  |
| 2026-09-19 | [`p1_spike_v112_execution_20260919.md`](p1_spike_v112_execution_20260919.md) | SPIKE V11.2 三项执行逻辑研究：能救回个别亏单，尚未得到正收益改法 | 结论：单独限制入场时效、沿用父 V9 止损、改用盘中高点激活原追踪，均未在 15m 或 1h 通过预先设定的研究门。15m 的 6 根时效限制有改善线索，但区间跨零、后段仍亏；统一放宽止损或提前追踪不能据此采用。没有搜索最优参数，没有修改 Pine、监控、默认参数或实盘。 |
| 2026-09-19 | [`p1_spike_v112_failure_diagnosis_20260919.md`](p1_spike_v112_failure_diagnosis_20260919.md) | SPIKE V11.2 失败交易诊断：先查入场有效性，再做局部参数检验 | 结论：现有数据足够提出并排序可检验的逻辑优化假设，不足以直接宣布某个参数最优。当前优先排查入场当根是否仍有支撑和推进，不能只围绕止盈参数优化。** 本次只对已有完整成交账本做诊断；未修改规则、未搜索参数、未重跑策略。 |
| 2026-09-19 | [`p1_spike_v112_manage_20260919.md`](p1_spike_v112_manage_20260919.md) | V9 多头用「突破」管理持仓：加仓变差；没突破就走只在下跌段有用 |  |
| 2026-09-19 | [`p1_spike_v112_sample_direction_20260919.md`](p1_spike_v112_sample_direction_20260919.md) | SPIKE 抽样 50 笔：方向、入场位置与利润兑现 | 结论：Owner 看到的“走势有向上机会”有数据支持，但需要明确起点。早期 V9 持仓中有 34/50 笔到过 1R；等待联合突破再入场只有 24/50，最终盈利 13/50。既有入场延迟，也有先止损后启动、以及盘中高点未触发收盘保护的问题。不能把所有亏损归为方向错，也不能据此说只改止盈就能盈利。 |
| 2026-09-19 | [`p1_spike_v112_selected_counts_20260919.md`](p1_spike_v112_selected_counts_20260919.md) | V11.2：Owner选定29币的15m、1h、4h交易数量 | 结论：排除Owner明确暂不研究的ARY后，29币共570笔入场：15m 409笔，1h 122笔，4h 39笔。其中568笔已平仓，4h的PEPE和XTZ各有1笔截至数据边界尚未平仓。 |
| 2026-09-19 | [`p1_spike_v112_selected_returns_20260919.md`](p1_spike_v112_selected_returns_20260919.md) | V11.2选定29币收益：15m受仓位口径影响，4h盈利集中于前段 | 结论：568笔已平仓中，15m合计−7.9723R但等名义金额每笔+0.3840%，1h合计−1.4711R/每笔−0.2334%，4h合计+53.5542R/每笔+6.9482%。4h仅37笔，DASH和BTC两笔贡献其净R利润83.9%，2025-09-10之后14笔已经转亏。三个周期相对... |
| 2026-09-19 | [`p1_spike_v112_support_20260919.md`](p1_spike_v112_support_20260919.md) | SPIKE V11.2 六均线支撑单变量完整回放 | 结论：两个周期均未通过本轮研究门，这项过滤不能据此升级为默认交易条件。 |
| 2026-09-19 | [`p1_spike_v112_tv_parity_20260919.md`](p1_spike_v112_tv_parity_20260919.md) | SPIKE V11.2：看板止损与 TradingView 缺信号核查 | 结论：GAS 已查到实际输入 K 线不一致，足以造成母 V9 和联合信号差异。“启用前”只是监控激活前的历史回算分类，不能解释全部缺信号，也不是 TV 一致性认证。TSLA、LIGHT 尚未定案；BTC 首次核查时 TV 选中 15m，卡片是 30m，尚不能直接比较。 |
| 2026-09-19 | [`p1_spike_v11_box_joint_20260918.md`](p1_spike_v11_box_joint_20260918.md) | SPIKE V11.1「多头框内突破就算」单独回测：不加限制后更差，1h 明显差于 V10.4 |  |
| 2026-09-19 | [`p1_spike_v11_box_trade_book_20260919.md`](p1_spike_v11_box_trade_book_20260919.md) | 突破+spike 逐笔明细 + 抽样 50 张图（框内规则） |  |
| 2026-09-18 | [`p1_spike_v10_4_1h_detail_20260918.md`](p1_spike_v10_4_1h_detail_20260918.md) | SPIKE V10.4 · 1h「突破+spike」只做多——逐笔拆解：挑点比随机好，但盈亏基本跟着大盘走 | 1. **信号本身有东西**：在 1h 上，突破+spike 选出来的做多点，比同币同月随机做多每笔好 0.24R，而且这个优势在前后两段都在（+0.31 / +0.17），与大盘涨跌几乎无关。 |
| 2026-09-18 | [`p1_spike_v10_4_1h_increment_20260918.md`](p1_spike_v10_4_1h_increment_20260918.md) | SPIKE V10.4 · 1h 复现与趋势线增量验证：原结果逐笔复现；联合门的「改善」主要是少做，不是做得更好 |  |
| 2026-09-18 | [`p1_spike_v10_4_joint_multitf_20260918.md`](p1_spike_v10_4_joint_multitf_20260918.md) | SPIKE V10.4「突破+spike」只做多 · 6 个周期回测——没有一个周期通过；1h 最接近 |  |
| 2026-09-18 | [`p1_spike_v10_long_break7_20260918.md`](p1_spike_v10_long_break7_20260918.md) | SPIKE V10（重做）：只做多 + 突破后 ≤7 根——每笔变好是因为笔数少了，后一段更差 |  |
| 2026-09-18 | [`p1_spike_v10_trendline_gate_20260918.md`](p1_spike_v10_trendline_gate_20260918.md) | SPIKE V10 趋势线突破门：每笔质量明显变好，总量砍掉一半以上，后一段依然为负 |  |
| 2026-09-18 | [`p1_spike_v11_mtf_joint_20260918.md`](p1_spike_v11_mtf_joint_20260918.md) | SPIKE V11 回测：加入上级周期突破，15m 和 1h 都没有变好，「突破+spike（上级突破）」后段反而最弱 |  |
| 2026-09-17 | [`p0_spike_v9_card_performance_20260917.md`](p0_spike_v9_card_performance_20260917.md) | V9 信号卡片接上持仓跟踪：R 与胜率不再是破折号 | 病因不是前端没接好，是后端从来没生产过前端要的那个对象。** 前端 `performanceView()` 早就会渲染 |
| 2026-09-17 | [`p1_chartart_bbrsi_v12_longonly_btceth_15m_20260917.md`](p1_chartart_bbrsi_v12_longonly_btceth_15m_20260917.md) | ChartArt BB+RSI v1.2 只做多：BTC / ETH USDT 永续 15m 预 holdout 回测 | 作者在 v1.2 里写的"改成只做多后回测更成功"，在 BTC/ETH 的长窗口上**确实成立**，但成立的原因不是入场变准了： |
| 2026-09-17 | [`p1_spike_v1_v9_asset_trim_20260917.md`](p1_spike_v1_v9_asset_trim_20260917.md) | SPIKE V1 / V9：剔除盈利最高与最低的币种后还剩什么 |  |
| 2026-09-17 | [`p1_spike_v9_later_year_attribution_20260917.md`](p1_spike_v9_later_year_attribution_20260917.md) | V9 后一年为什么会亏：毛优势塌到成本线以下，塌的地方是 30m |  |
| 2026-09-16 | [`p1_owner_manual_order_card_20260916.md`](p1_owner_manual_order_card_20260916.md) | 人工开单卡 · 均线密集启动 |  |
| 2026-09-16 | [`p1_owner_manual_trading_system_20260916.md`](p1_owner_manual_trading_system_20260916.md) | 人工交易手册：均线密集、V9与YOLO怎样一起用 |  |
| 2026-09-15 | [`p1_goal_martingale_path_20260915.md`](p1_goal_martingale_path_20260915.md) | 倍投可行路径搜索 · 第 1 轮：前提检验 | 1. **ETH × V8 在 3m/15m/30m/60m/240m 全都没有可信的毛 edge。** 10 个格子的毛 R |
| 2026-09-15 | [`p1_goal_martingale_path_r2_20260915.md`](p1_goal_martingale_path_r2_20260915.md) | 倍投可行路径搜索 · 第 2 轮：行情能否事前识别 & 亏后加码是否成立 | 1. **没有任何事前市场状态门通过检验。** 5 个严格因果的门，Holm 校正后 p 全部 = 1.0000。 |
| 2026-09-15 | [`p1_goal_martingale_path_r3_20260915.md`](p1_goal_martingale_path_r3_20260915.md) | 倍投可行路径搜索 · 第 3 轮：全仓库普查 + 破产概率 | 1. **扫描 35523 个账本文件、116 个实验，42 个可检验格子：10 个均值净 R 为正， |
| 2026-09-15 | [`p1_goal_martingale_path_r4_20260915.md`](p1_goal_martingale_path_r4_20260915.md) | 倍投可行路径搜索 · 第 4 轮：零期望压力测试与亏损聚集分解 | 1. **"倍投是三者中最差"在全部 6 个格子成立**（3 个期望档 × 2 种序列生成方式）。 |
| 2026-09-15 | [`p1_ifvg_lrl_eth_20260915.md`](p1_ifvg_lrl_eth_20260915.md) | ETH 3分钟 iFVG＋LRL v1：未显示扣费后盈利能力 | 验证段（2025-01-01至2026-05-01）单仓 LRL 组完成 **24 笔**，毛收益 **-4.00R**，扣0.2%往返成本后 **-22.89R**，净胜率 **37.50%**，净PF **0.204**。同口径单独iFVG为300笔、-185.70R。两组交易数量不同，不... |
| 2026-09-15 | [`p1_spike_v8_ict_be2_adoption_20260915.md`](p1_spike_v8_ict_be2_adoption_20260915.md) | ETH 15分钟＋ICT：加入净2R后的含费保本 |  |
| 2026-09-15 | [`p1_spike_v8_ict_multitf_20260915.md`](p1_spike_v8_ict_multitf_20260915.md) | 原V8 × ICT时段：六周期对照 |  |
| 2026-09-15 | [`p1_spike_v9_eth_lowtf_20260915.md`](p1_spike_v9_eth_lowtf_20260915.md) | ETH V9 · 3m / 5m 完整历史回测 | V9 比 V8 少亏，但 ETH 3m 和 5m 仍全部亏损。3m 净收益 −935.30R → **−773.89R**，5m −624.48R → **−476.81R**；净 R 利润因子分别只有 **0.4966 / 0.6910**。原始分段的前后两段也都没有转正。 |
| 2026-09-15 | [`p1_spike_v9_full_backtest_20260915.md`](p1_spike_v9_full_backtest_20260915.md) | SPIKE V9 全量回测：优于 V8，但后一年仍亏损 |  |
| 2026-09-15 | [`p1_spike_v9_implementation_20260915.md`](p1_spike_v9_implementation_20260915.md) | SPIKE V9：标的、量比与 UTC 周日过滤已实现 |  |
| 2026-09-15 | [`p1_spike_v9_v1_comparison_20260915.md`](p1_spike_v9_v1_comparison_20260915.md) | SPIKE V9 与 V1：已有全量结果对读 |  |
| 2026-09-14 | [`p0_gold_ma_indicator_20260914.md`](p0_gold_ma_indicator_20260914.md) | 金标形态指标：目标纠偏与首轮回放（2026-09-14） |  |
| 2026-09-14 | [`p0_tv_release_4h_logic_20260914.md`](p0_tv_release_4h_logic_20260914.md) | release-20260201-回放样式：ETH 4H 逻辑与回测口径核对 |  |
| 2026-09-14 | [`p1_release_eth_multitf_20260914.md`](p1_release_eth_multitf_20260914.md) | P1 ETH multi-timeframe release replay — 2026-09-14 | 结论：4h 原规则有抓长趋势的能力，但高杠杆把风险放得过大；本轮只有 15m 的 SMA80 候选值得保留观察，1h 与4h 的信号优化没有通过后续验证。没有任何版本达到本项目的可靠性准入。 |
| 2026-09-14 | [`p1_spike_eth3m_net_recovery_20260914.md`](p1_spike_eth3m_net_recovery_20260914.md) | ETH3m：净1R止盈、费用保本与整轮回本重置验证 |  |
| 2026-09-14 | [`p1_spike_eth3m_partial_tp_20260914.md`](p1_spike_eth3m_partial_tp_20260914.md) | ETHUSDT.P 3分钟：分批止盈和收紧移动止损 |  |
| 2026-09-14 | [`p1_spike_eth3m_recovery_20260914.md`](p1_spike_eth3m_recovery_20260914.md) | ETHUSDT.P 3分钟：1U起步的倍投与回本研究 |  |
| 2026-09-14 | [`p1_spike_eth_lowtf_cost_diagnostic_20260914.md`](p1_spike_eth_lowtf_cost_diagnostic_20260914.md) | ETH V8 低周期成本预算与 1R 保本诊断 |  |
| 2026-09-14 | [`p1_spike_fanshen_exit_multitf_20260914.md`](p1_spike_fanshen_exit_multitf_20260914.md) | V8 × 翻身 Stoch：六周期退出回测 |  |
| 2026-09-14 | [`p1_spike_fanshen_source_audit_20260914.md`](p1_spike_fanshen_source_audit_20260914.md) | 翻身 V1 源码核验：找到旧 Stoch，尚未找到目标完整版本 | Notion 中找到一份完整的 Stochastic with Signal & Alert Pine v6 源码，保存在「bonk 1h」，页面最后编辑时间为 2025-07-06 11:27:32 UTC。它的简称是 Stoch，但缺少当前 TradingView「翻身版本V1-2025-... |
| 2026-09-14 | [`p1_spike_v1_v8_asset_ranking_20260914.md`](p1_spike_v1_v8_asset_ranking_20260914.md) | V1与V8：币种收益榜、赢家特征与差币排除检验 |  |
| 2026-09-14 | [`p1_spike_v1_v8_be05_20260914.md`](p1_spike_v1_v8_be05_20260914.md) | V1 / V8：浮盈触及 0.5R 后推开仓价，效果如何 |  |
| 2026-09-14 | [`p1_spike_v1_v8_tier_lock_20260914.md`](p1_spike_v1_v8_tier_lock_20260914.md) | V1 / V8：0.5R保本＋1.5R锁0.5R，三组对照 | 原版V1（多头）：相同入场、三组均已结束的6,116笔，原退出 854.70R → 单独保本 1,042.87R → 分档 1,057.65R。分档相较原退出 202.95R，相较单独保本 14.78R。 |
| 2026-09-14 | [`p1_spike_v8_entry_evidence_20260914.md`](p1_spike_v8_entry_evidence_20260914.md) | V8：BB压缩段排列、六线密集与高周期入场反证 | 结论：三种想法已分别检验，没有找到可直接提高全池收益与胜率的统一硬过滤。** BB段同向排列有小幅胜率线索，但删掉的大赢家太多；BB与六线密集叠加没有跨年稳定改善；明确高周期反对的覆盖很小。更有价值的线索是周期与方向差异，以及“旧排列”与“价格已转向”要分开。本报告保留失败结果、完整补集和六个... |
| 2026-09-14 | [`p1_spike_v8_six_filters_20260914.md`](p1_spike_v8_six_filters_20260914.md) | V8：六批 V1 观察的独立过滤回放（2026-09-14） | 九个独立入场过滤和一个独立退出方案均已完成。**这些规则中有局部改善，但没有一条已经证明能把 V8 变成高胜率、稳定盈利的系统。** 本轮只测试冻结 V8；旧 V1 数字仅复核来源，没有把 V1 的静态删单结果当成 V8 的反事实收益。 |
| 2026-09-13 | [`p0_ai_strategy_factory_workflow_audit_20260913.md`](p0_ai_strategy_factory_workflow_audit_20260913.md) | AI 策略工厂能否赚钱，以及怎样接入当前 SPIKE |  |
| 2026-09-13 | [`p0_comp_ma_sequence_case_20260913.md`](p0_comp_ma_sequence_case_20260913.md) | COMP 1H：密集、排列与扩散的先后顺序 |  |
| 2026-09-13 | [`p1_spike_v8_entry_process_early_exit_20260913.md`](p1_spike_v8_entry_process_early_exit_20260913.md) | V8：证据是否过时、失败突破反转与提前退出 | “证据老了就不要做”目前不能成为 V8 的默认过滤：减少的亏损伴随大量真正10R赢家丢失。“放量突破失败后反向破位”有相对区分力，但不是稳定赚钱保证，尤其多空不能混用。四种提前退出方案均未通过开发期标准，后一年也未显示足以弥补大趋势丢失的整体改善。 |
| 2026-09-12 | [`p0_spike_v1_plus_implementation_20260912.md`](p0_spike_v1_plus_implementation_20260912.md) | SPIKE 强劲爆发 V1+：保护与因果参考实现（2026-09-12） | 新增独立 Pine 指标 `SPIKE 强劲爆发 V1+`（短名 `SPIKE V1+`）。它保留 V1 的双向原始 |
| 2026-09-12 | [`p1_spike_v6_wvf_20260912.md`](p1_spike_v6_wvf_20260912.md) | SPIKE V6 × Williams Vix Fix：组合回测与完整走势 | 本轮两种 WVF 硬过滤都不适合作为 V6 所有多头启动的必选条件。** 后一年多头交易减少，但胜率、PF、已实现净 R 同时变差，并且漏掉多数原版 10R 赢家。前一年的过滤后 PF 提升，没有在后一年延续。 |
| 2026-09-11 | [`p0_spike_v1_monitor_migration_20260911.md`](p0_spike_v1_monitor_migration_20260911.md) | SPIKE V1 Mac monitor migration：信号浏览、冻结回放图与服务验收记录 | 本轮把本机 `127.0.0.1:8766` 的监控协议收敛到冻结的 **SPIKE V1 长多 30m / 1H / 4H**：收盘后的原始 V1 启动和额外 YOLO 确认是独立事件、独立 Bark 阶段；历史回放只用于浏览且绝不补发 Bark。已导入的 1,019 条 V1 回放信号可按... |
| 2026-09-11 | [`p0_spike_v1_okx_133_review_20260911.md`](p0_spike_v1_okx_133_review_20260911.md) | SPIKE Burst V1 · OKX 133 笔逐笔图册（2026-09-11） | 本交付是历史图形复盘数据，不训练、不调参、不重跑收益，也不触及监控、通知、订单或 |
| 2026-09-11 | [`p1_spike_v1_eth_stops_20260911.md`](p1_spike_v1_eth_stops_20260911.md) | ETH-USDT-SWAP：原版 SPIKE Burst V1 / V6 的止损敏感性 | 原版 V1 的 ETH 样本过小，不能命名“最优止损”。30 分钟有 11 笔，表现主要由一笔 +19.83R 交易贡献；1 小时只有 3 笔且全亏，4 小时只有 1 笔。本轮**没有充分证据**改动默认 2 ATR 初始止损和 4 ATR 跟随止损。初始 ATR floor 的 1.5–3.... |
| 2026-09-11 | [`p1_spike_v1_replay_ledger_link_20260911.md`](p1_spike_v1_replay_ledger_link_20260911.md) | SPIKE V1 回放记录与 v2 覆盖账本逐笔关联 | 2026-09-11（北京时间）已将不可变 snapshot 中全部 **6,185** 条 30m / 1H / 4H `source=replay, confirmation=raw` 的 V1 信号导入 monitor，并按四元组 `venue / symbol / timeframe_... |
| 2026-09-11 | [`p1_spike_v1_twoyear_20260911.md`](p1_spike_v1_twoyear_20260911.md) | SPIKE 强劲爆发 V1：两年三所全市场回测 — 覆盖受限账本的可执行口径修正 | 2026-09-11（北京时间）完成了覆盖受限 V1 账本的经济口径重建。它读取的是本轮运行开始时已落盘、带收据的 OHLC CSV；本阶段没有发起行情抓取、训练、参数搜索、通知、模型切换或交易操作。当前覆盖仍不足以构成“三所全市场”或收益有效性的结论：固定分母 **6,724** 个 cur... |
| 2026-09-11 | [`p1_spike_v5_display_history_20260911.md`](p1_spike_v5_display_history_20260911.md) | V5 显示与历史盈亏框修复 |  |
| 2026-09-11 | [`p1_spike_v6_eth_freqtrade_20260911.md`](p1_spike_v6_eth_freqtrade_20260911.md) | SPIKE V6 ETH Freqtrade bridge：冻结镜像执行检查 |  |
| 2026-09-11 | [`p1_spike_v6_volume_price_20260911.md`](p1_spike_v6_volume_price_20260911.md) | SPIKE V6：量价结构证据的 PEPE 1H 功能核对 | V6 只增加一个条件：V5 的冻结 V4 来源等待期间，必须已有一根满足 V1 方向实体质量的 K 线，并且它与 V5 既有的三根推进和三根量比包络同时成立。它不恢复 V1 的单根 RV≥4 或 TR/ATR≥3 硬门。 |
| 2026-09-10 | [`p0_codex_subagents_review_20260910.md`](p0_codex_subagents_review_20260910.md) | VoltAgent Awesome Codex Subagents：源码审阅与项目适配分析 | 结论：值得作为角色模板库借鉴；是否提升任务质量，需要在本项目验证。对 fable-trading，建议选择少量角色，保留清晰的分工与证据格式，再补齐本仓研究纪律。 |
| 2026-09-10 | [`p0_spike_burst_indicator_20260910.md`](p0_spike_burst_indicator_20260910.md) | SPIKE 强劲爆发 V1：独立指标工程验收 |  |
| 2026-09-10 | [`p0_spike_burst_risk_display_20260910.md`](p0_spike_burst_risk_display_20260910.md) | SPIKE 强劲爆发 V1：盈亏框恢复验收 |  |
| 2026-09-10 | [`p1_altseason_donchian_ewmac_20260910.md`](p1_altseason_donchian_ewmac_20260910.md) | 山寨强势行情：Donchian 与 EWMAC 固定规则回测 | 下表为52个等初始资本分账户、同币单仓、每次1%分账户风险预算且现金封顶的历史模拟持仓路径。未上市或预热不足的份额保持现金。现金买持采用全额资本，风险水平不同；随机组合使用相同策略风险与退出。所有数字只扣固定0.2%入场名义往返成本，未含完整资金费。 |
| 2026-09-10 | [`p1_spike_burst_validation_20260910.md`](p1_spike_burst_validation_20260910.md) | SPIKE 强劲爆发 V1：抓到了哪些行情，实际留下多少利润 | 新指标能找到部分强劲爆发，并用跟踪退出留下大幅利润；但当前规则加上固定账户分配，还没有证明能稳定优于对照。 |
| 2026-09-10 | [`p1_spike_v5_bidirectional_20260910.md`](p1_spike_v5_bidirectional_20260910.md) | V5 多空确认、白色信号 K 线与无边框盈亏区 |  |
| 2026-09-09 | [`p0_altcoin_rotation_system_20260909.md`](p0_altcoin_rotation_system_20260909.md) | 选择性山寨观察系统 v1：已跑通，尚未验证收益 |  |
| 2026-09-09 | [`p0_imacd_v27_risk_reference_20260909.md`](p0_imacd_v27_risk_reference_20260909.md) | IMACD V2.7：结构止损与真实趋势空间 | 旧版止损直接取释放K线极值，容易因信号当根较小而生成极窄风险。旧版没有“达到3R就止盈”的执行条件，绿色框取`max(3, 已走最大R)`；它还会因为触损、主线回零、结构破坏或新释放接管而停止跟踪。 |
| 2026-09-09 | [`p0_spike_gainers_audit_20260909.md`](p0_spike_gainers_audit_20260909.md) | Spike 涨幅榜信号核对 · 2026-09-09 | 截图指定的11个币，在今天当前四周期的多头“蓄势释放”口径下分成三类：**9个没有新释放、GRASS已触发且Bark服务器接受、ZEC的30m释放早于该周期启用**。不能将上涨名单中的每个币都视为一次通知漏发。 |
| 2026-09-09 | [`p1_imacd_altcoin_trends_20260909.md`](p1_imacd_altcoin_trends_20260909.md) | Spike｜高波动山寨启动与趋势持有研究 | 逐币列出默认值与山寨旧年提名候选，未在BTC/ETH上重新选参。此处净收益、回撤是该币单独一个资本袖套，不是54币组合；不能与主高波动组合绝对收益直接比较。 |
| 2026-09-08 | [`p0_imacd_pine_retest_20260908.md`](p0_imacd_pine_retest_20260908.md) | IMACD：清晰零轴、信号价格与 SMA20 影线回踩 |  |
| 2026-09-08 | [`p0_imacd_telegram_chart_20260908.md`](p0_imacd_telegram_chart_20260908.md) | Telegram 简讯与信号图验收 · 2026-09-08 |  |
| 2026-09-08 | [`p0_imacd_tv_risk_box_20260908.md`](p0_imacd_tv_risk_box_20260908.md) | TradingView IMACD 启动 K 线盈亏比框：工程验收 | 结论：V2.4 已在 TradingView 编译成功，云端脚本与桌面图表均已保存。** 已确认的主图蓄势释放信号现在带有红绿盈亏比参考框，以及入场、止损、目标三档价格。原信号核心、六条均线、副图双线与零轴保持原样。本次没有收益评估，也没有修改或重启 TG、Bark、Mac 监控服务。 |
| 2026-09-08 | [`p1_yolo_dataset_consolidation_20260908.md`](p1_yolo_dataset_consolidation_20260908.md) | 最新模型1043事件已带原训练框进入人工审核 |  |
| 2026-09-08 | [`p1_yolo_historical_inventory_20260908.md`](p1_yolo_historical_inventory_20260908.md) | 旧2513题已建处置清单，继续优先审核最新1043题 |  |
| 2026-09-08 | [`p1_yolo_review_entry_fix_20260908.md`](p1_yolo_review_entry_fix_20260908.md) | YOLO本周1043题审核入口已修正 |  |
| 2026-09-08 | [`p1_yolo_review_future150_20260908.md`](p1_yolo_review_future150_20260908.md) | 本周YOLO审核已扩展到150根未来K线 |  |
| 2026-09-08 | [`week_plan_yolo_20260908.md`](week_plan_yolo_20260908.md) | YOLO 本周优化计划｜2026-09-08—09-13 | 结论：现在最值得投入的是“你调整的答案能可靠进入训练，并能在独立样本上验收”。继续安装框架、扩大预框数量或直接重跑旧数据，不能解决这条缺口。 |
| 2026-09-07 | [`p0_imacd_ma_mtf_20260907.md`](p0_imacd_ma_mtf_20260907.md) | IMACD＋六均线密集＋多周期：找到有效改进，也找到过滤大趋势的原因 |  |
| 2026-09-07 | [`p0_imacd_okx_multitimeframe_20260907.md`](p0_imacd_okx_multitimeframe_20260907.md) | Impulse MACD 34/9：OKX BTCUSDT.P / ETHUSDT.P 多周期分析 | 结论：它适合表达“价格相对平滑通道的偏离及偏离变化”，但当前金叉/死叉箭头把趋势延续、逆势修复与回到中性区混在了一起，不能直接当作统一买卖规则。** 本次原参数统计中，BTC、ETH 的 15m、1h、4h 交叉有约52%–54%发生在与箭头方向相反的 md 区域。这个数字不是错误率或亏损率。 |
| 2026-09-07 | [`p0_imacd_pine_focus_20260907.md`](p0_imacd_pine_focus_20260907.md) | IMACD 蓄势释放：长横盘与首次扩张的视觉重点 |  |
| 2026-09-07 | [`p0_imacd_profit_mechanism_20260907.md`](p0_imacd_profit_mechanism_20260907.md) | IMACD：零轴横盘后启动，利润怎样留在手里 |  |
| 2026-09-07 | [`p0_pine_allin_eth4h_20260907.md`](p0_pine_allin_eth4h_20260907.md) | ALLIN V7：ETHUSDT 永续 4H 原码回放 |  |
| 2026-09-07 | [`p1_15m_grade_a_assisted_future40_20260907.md`](p1_15m_grade_a_assisted_future40_20260907.md) | YOLO 人工审核简化与未来 40 根对照 |  |
| 2026-09-07 | [`p1_15m_grade_a_labelstudio_manual_20260907.md`](p1_15m_grade_a_labelstudio_manual_20260907.md) | 完整 YOLO 数据已转入 Label Studio 手工标注 |  |
| 2026-09-07 | [`p1_15m_grade_a_owner_calibration_20260907.md`](p1_15m_grade_a_owner_calibration_20260907.md) | YOLO 提准第一步：盲审校准包与单变量训练准备 | 结论：人工审核已可开始，模型准确率尚未改善或重新验收。** 已生成 240 个去重候选事件、加入 36 次盲重复，共 276 题；审核页支持类别、核心边界与框义裁决，并能保存到本机、恢复与导入。下一轮训练只改变经 Owner 确认的难负例来源，等待真实答案和可行匹配后继续。 |
| 2026-09-07 | [`p1_btcusdtp_hourly_fixed_clock_v24_20260907.md`](p1_btcusdtp_hourly_fixed_clock_v24_20260907.md) | Entry Persistence Audit | 当前这批入口的优势仍未获支持，不能把平均亏损全解释成“止盈太早”。** 保留2023–2024年全部251个原始K1入口，脱离原来的止损、小周期颜色退出，在预先指定的4h时钟观察：平均毛markout仅+1.0056bp，减固定20bp成本后为−18.9944bp，95%月簇区间[−30.70... |
| 2026-09-07 | [`p1_btcusdtp_hourly_structure_event_support_v25_20260907.md`](p1_btcusdtp_hourly_structure_event_support_v25_20260907.md) | Structure Event Support | 冻结的251个原始K1入口中，仅9笔（3.59%）在自身K1本根首次建立或翻转同向结构。 |
| 2026-09-07 | [`p1_yolo_historical_label_reuse_20260907.md`](p1_yolo_historical_label_reuse_20260907.md) | P1 · 历史人工标注库存与复用顺序（2026-09-07） | 旧人工标注仍有用，应先恢复其来源和确认状态，再决定需要补标多少。当前 4,172 个事件的 |
| 2026-09-07 | [`p1_yolo_owner_box_curation_20260907.md`](p1_yolo_owner_box_curation_20260907.md) | YOLO 历史框审核：已装工具接入与重点复核队列 | Datumaro、CleanVision 已实际检查完整批次，FiftyOne 已持久化收录 2,513 张主审核图与建议框。Label Studio 继续使用原项目 77，不新增项目。人工入口是[先审：重点 50 张](http://127.0.0.1:8081/projects/77/da... |
| 2026-09-07 | [`p1_yolo_owner_box_refinement_20260907.md`](p1_yolo_owner_box_refinement_20260907.md) | 历史人工框细化：2513个建议已进入Label Studio |  |
| 2026-09-05 | [`p1_altcoin_1d_k1k2_early_launch_holdout_20260905.md`](p1_altcoin_1d_k1k2_early_launch_holdout_20260905.md) | 山寨币日线 K1→K2：早启动 V4 最终 Holdout | 裁决：**REJECT / 本配置 holdout 已消费 1 次 / 不重跑 / 不写 TradingView / 不改生产状态 |
| 2026-09-05 | [`p1_altcoin_1d_k1k2_episode_runner_20260905.md`](p1_altcoin_1d_k1k2_episode_runner_20260905.md) | 山寨币日线 K1→K2：稀疏趋势 Episode 与动态退出审计 | 本轮不是把 15m 或 SMA40 参数放大到日线。V1 在 52 个当前本地缓存的 OKX 山寨币永续上，独立探索了 |
| 2026-09-05 | [`p1_altcoin_1d_k1k2_market_context_20260905.md`](p1_altcoin_1d_k1k2_market_context_20260905.md) | 山寨币日线 K1→K2：市场广度共振 V3 与趋势接管诊断 | 裁决：**REJECT / confirmation B 保持封存 / 不写 TradingView / 不改 ACTIVE、forward 或实盘 |
| 2026-09-05 | [`p1_eth_xau_15m_asset_specific_k1k2_20260905.md`](p1_eth_xau_15m_asset_specific_k1k2_20260905.md) | ETH / XAU 15m：品种专属 K1→K2 趋势策略审计 | 本轮没有把 BTC 参数直接复制到 ETH 和黄金，而是分别预注册并按时间顺序选择：触线均线组合、 |
| 2026-09-05 | [`p1_ethusdtp_15m_causal_confluence_20260905.md`](p1_ethusdtp_15m_causal_confluence_20260905.md) | ETHUSDT.P 15m：因果共振筛选与扩张门外推失败审计（V17/V18） | 本轮把“加入一些共振”真正做成了两级时间外验证，而不是往脚本里多堆几个勾选项。先在冻结的 |
| 2026-09-05 | [`p1_ethusdtp_15m_gradual_take_profit_20260905.md`](p1_ethusdtp_15m_gradual_take_profit_20260905.md) | P1：ETHUSDT.P 15m 趋势单渐进止盈 V16（2026-09-05） | Owner 指出的执行问题成立：真实止盈和抬高止损是两件事，不能互相冒充。** 本轮冻结入场和原 |
| 2026-09-04 | [`p0_two_key_candle_ma_retest_deep_dive_20260904.md`](p0_two_key_candle_ma_retest_deep_dive_20260904.md) | P0 — 两根关键 K 线 + SMA40 回踩：55 维因果拆解与盈利性证伪（2026-09-04） | 问题 \| 结论 \| |
| 2026-09-04 | [`p0_two_key_candle_sma40_pine_indicator_20260904.md`](p0_two_key_candle_sma40_pine_indicator_20260904.md) | P0 — 双关键 K 线 + SMA40 回踩 Pine v6 指标交付（2026-09-04） |  |
| 2026-09-04 | [`p1_1h_filusdt_model_first_breakout_gate_20260904.md`](p1_1h_filusdt_model_first_breakout_gate_20260904.md) | FILUSDT.P 1h：模型先检测、代码再确认站上线（2026-09-04） | Owner 提出的流水线顺序是对的，但当前 YOLO 仍然给不出早信号。** 本轮严格执行： |
| 2026-09-04 | [`p1_1h_filusdt_model_first_standing_gate_20260904.md`](p1_1h_filusdt_model_first_standing_gate_20260904.md) | P1 — FILUSDT.P 1h 模型先、当前站位代码后诊断（2026-09-04） | Owner 要求去掉代码层的前一根首次穿越条件。本轮保留“模型先提案、代码后确认”的顺序， |
| 2026-09-04 | [`p1_1h_okx_model_first_standing_top10_20260904.md`](p1_1h_okx_model_first_standing_top10_20260904.md) | P1 — OKX 全市场 1h：模型先检测、当前站位代码后 Top-10（2026-09-04） | 已按 Owner 要求**取消“只看已冻结 pre-holdout 候选”限制**，重新下载当时全部合资格 OKX |
| 2026-09-04 | [`p1_btcusdtp_15m_multifactor_confluence_20260904.md`](p1_btcusdtp_15m_multifactor_confluence_20260904.md) | P1：BTCUSDT.P 15m 多因子共振与特征工程审计（2026-09-04） | 这轮已经把能从当前因果数据中落地的共振维度系统跑过一遍：K1→K2 关系、趋势年龄、完整 1h |
| 2026-09-04 | [`p1_btcusdtp_15m_runner_isolation_20260904.md`](p1_btcusdtp_15m_runner_isolation_20260904.md) | P1：BTCUSDT.P 15m 趋势接管交易可识别性审计（2026-09-04） | 目前不能在 K2 入场时稳定地“只抓住”那 170 笔趋势接管交易。** 170 笔平均净收益 |
| 2026-09-04 | [`p1_btcusdtp_15m_trend_refactor_20260904.md`](p1_btcusdtp_15m_trend_refactor_20260904.md) | P1：BTCUSDT.P 15m K1→K2 高召回与均线趋势退出重构（2026-09-04） | Owner 对退出方式的判断是对的：**明显启动后的盈利仓，不应被固定 TP 提前截断，而应在趋势仍成立时跟随均线。 |
| 2026-09-04 | [`p1_btcusdtp_15m_trend_regime_live_entry_20260904.md`](p1_btcusdtp_15m_trend_regime_live_entry_20260904.md) | P1：BTCUSDT.P 15m 趋势状态去重与实时活性门审计（2026-09-04） | 用户截图中的“四组信号”不是四段趋势，而是同一震荡区间被旧脚本反复当作新机会。根因有两层： |
| 2026-09-04 | [`p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md`](p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md) | P1：BTCUSDT.P 1h K1→K2 Owner Causal V2 回测（2026-09-04） | 修改有效地减少了坏交易，但仍没有达到可交易标准，结论为 RESEARCH KEEP / PRODUCTION REJECT。 |
| 2026-09-04 | [`p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904_erratum.md`](p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904_erratum.md) | 勘误：BTCUSDT.P 1h Owner Causal V2 的 holdout 读取声明 | 原报告关于 `data/kline_deep/okx_BTC_USDT_SWAP_15m_158499.csv` “物理截止于 2026-02-28、holdout 读取 0 行”的声明是错误的。 |
| 2026-09-04 | [`p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md`](p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md) | P1：BTCUSDT.P 1h Pine v8 近半年逐笔回测（2026-09-04） | 这套默认参数不具备可确认的稳定盈利能力，结论为 REJECT。** 2026-03-04 00:00 至 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md`](p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md) | BTCUSDT.P 15min / 5min 独立 K1→K2 研究（2026-09-04） |  |
| 2026-09-04 | [`p1_btcusdtp_k1k2_15m_5m_parameter_optimization_preholdout_20260904.md`](p1_btcusdtp_k1k2_15m_5m_parameter_optimization_preholdout_20260904.md) | BTCUSDT.P 15m / 5m K1→K2 独立参数优化（pre-holdout） | 结论：**15m 与 5m 均未通过冻结验证，不可用于实盘。 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_15m_dynamic_stop_preholdout_20260904.md`](p1_btcusdtp_k1k2_15m_dynamic_stop_preholdout_20260904.md) | BTCUSDT.P 15m K1→K2 动态 / 自动止损实验（2026-09-04） | 预注册门 \| 最好观察值 \| 结果 \| |
| 2026-09-04 | [`p1_btcusdtp_k1k2_15m_gap_min_confirmation_20260904.md`](p1_btcusdtp_k1k2_15m_gap_min_confirmation_20260904.md) | BTCUSDT.P 15min K1→K2 最小距离确认（2026-09-04） | 门 \| 结果 \| 通过？ \| |
| 2026-09-04 | [`p1_btcusdtp_k1k2_15m_two_stage_k2_freqtrade_preholdout_20260904.md`](p1_btcusdtp_k1k2_15m_two_stage_k2_freqtrade_preholdout_20260904.md) | BTCUSDT.P 15m K1→K2 两阶段确认 + Freqtrade 全量验证 |  |
| 2026-09-04 | [`p1_btcusdtp_k1k2_breakout_entry_preholdout_20260904.md`](p1_btcusdtp_k1k2_breakout_entry_preholdout_20260904.md) | BTCUSDT.P K1→K2：方向突破确认入场（15m / 5m） | 本轮只改变入场：K2 后 15/30/60/120 分钟内，等待第一根方向 K 线收盘突破 K2 方向极值（多头 K2 高、空头 K2 低）且仍在当时均线方向侧，下一根开盘入场。原 K2 对侧极值止损、3R、12 小时、1.5R 保护、6 小时冷却、风险门和 20bp 成本不变。 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_causal_failure_map_20260904.md`](p1_btcusdtp_k1k2_causal_failure_map_20260904.md) | BTCUSDT.P 15min / 5min K1→K2 因果失败地图（2026-09-04） | 结论：**15min 仅提名 `K1→K2 gap >= 5 bars`；5min 没有任何稳定单变量可提名。此报告不改变交易规则。 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_fixed_target_preholdout_20260904.md`](p1_btcusdtp_k1k2_fixed_target_preholdout_20260904.md) | BTCUSDT.P K1→K2：固定止盈倍数（15m / 5m） | 用户关于“许多盈利交易在 3R 后仍能走远”的观察成立，但**不能直接推出整单止盈应抬高**。本轮只把固定目标从 3R 改为 2/4/5/6/8R，其余全部冻结。 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_partial_runner_preholdout_20260904.md`](p1_btcusdtp_k1k2_partial_runner_preholdout_20260904.md) | BTCUSDT.P K1→K2：3R 部分止盈 + 8R Runner（15m / 5m） | 本轮把用户看到的长赢家尾部做成可执行的分批退出：首次触及 3R 时，保留 0%、10%、25%、50%、75% 或 100% 仓位继续看 8R；其余仓位在 3R 兑现。信号、入场、K2 止损、1.5R 收盘保护、12 小时、冷却和一次性加权 20bp 成本全部冻结。 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_protection_trigger_preholdout_20260904.md`](p1_btcusdtp_k1k2_protection_trigger_preholdout_20260904.md) | BTCUSDT.P K1→K2：盈利保护触发点（15m / 5m） | 本轮只改变一件事：收盘浮盈达到多少 R 后，从下一根 K 线起把止损抬到覆盖 20bp 往返成本的位置。测试 0.75、1.00、1.25、1.50、2.00、2.50R 和完全关闭保护；信号、立即次开盘入场、K2 极值止损、3R 止盈、12 小时持有上限、6 小时冷却和成本全部冻结。 |
| 2026-09-04 | [`p1_btcusdtp_k1k2_stop_buffer_preholdout_20260904.md`](p1_btcusdtp_k1k2_stop_buffer_preholdout_20260904.md) | BTCUSDT.P K1→K2：K2 极值外 ATR 止损缓冲实验（15m / 5m） | “很多止损后来又走到 3R”不等于“把止损放宽就能救回来”。在冻结信号、下一根开盘入场、3R 目标、12 小时持有、1.5R 盈利保护和 20bp 往返成本后，只把 K2 极值外缓冲从 0 增至 0.10/0.20/0.30/0.50 ATR： |
| 2026-09-04 | [`p1_btcusdtp_k1k2_sweep_reclaim_entry_preholdout_20260904.md`](p1_btcusdtp_k1k2_sweep_reclaim_entry_preholdout_20260904.md) | BTCUSDT.P K1→K2：扫过 K2 极值后收复入场（15m / 5m） | 本轮只改变入场规则：基准为 K2 完成后下一根开盘；候选要求随后 15/30/60/120 分钟内，先严格扫过原 K2 极值，再由一根方向 K 线收盘收回 K2 极值和当时均线，下一根开盘才进场。原 K2 极值止损、3R、12 小时、1.5R 保护、6 小时冷却和 20bp 成本全部冻结。 |
| 2026-09-03 | [`p1_15m_ma_launch_grade_a_daily_movers_202510_20260903.md`](p1_15m_ma_launch_grade_a_daily_movers_202510_20260903.md) | 2025-10 每日涨跌幅 Top5+Top5：Grade-A 15m 数据集候选挖掘（2026-09-03） | 已用当前 **Grade-A 8k 正例 + 24k 负例、full40、native-1280** 检测器，把 |
| 2026-09-03 | [`p1_15m_ma_launch_grade_a_daily_movers_5000_20260903.md`](p1_15m_ma_launch_grade_a_daily_movers_5000_20260903.md) | 每日涨跌幅 Top5+Top5：Grade-A 15m 5,000+ 候选挖掘（2026-09-03） | 已按预注册顺序从 **2025-10 向前逐个完整 UTC 月**扫描 Binance USD-M 15m 日涨幅 |
| 2026-09-03 | [`p1_15m_ma_launch_owner_grade_a8000_neg24000_hl2_train1280_20260903.md`](p1_15m_ma_launch_owner_grade_a8000_neg24000_hl2_train1280_20260903.md) | 15m Grade-A 六均线 close→HL2 单变量复训（2026-09-03/04） | HL2 有“正例更容易被检出”的迹象，但没有证明整体优于 close，本轮不建议替换现有表示。 |
| 2026-09-03 | [`p1_15m_yolo_color_semantics_audit_20260903.md`](p1_15m_yolo_color_semantics_audit_20260903.md) | 15m YOLO K 线与六均线颜色语义审计（2026-09-03） | 1. **现在不应把涨跌 K 线直接改成完全同色。** 对单根实心蜡烛而言，实体上下端只给出 |
| 2026-09-03 | [`p1_1h_filusdt_grade_a_recent5d_probe_20260903.md`](p1_1h_filusdt_grade_a_recent5d_probe_20260903.md) | FILUSDT.P 1h 最近5天：冻结 Grade-A 模型逐小时回放（2026-09-03/04） | 同一段上涨被模型检出了，但并不是在下降趋势线刚突破时提前检出。** 冻结的 Grade-A |
| 2026-09-03 | [`p1_btcusdtp_ma_smoothness_visual_comparison_20260903.md`](p1_btcusdtp_ma_smoothness_visual_comparison_20260903.md) | BTCUSDT.P 六均线平滑度视觉对照（2026-09-03） |  |
| 2026-09-03 | [`p1_crypto_grade_a_yolo_mtf_latest_20260903.md`](p1_crypto_grade_a_yolo_mtf_latest_20260903.md) | P1：最新加密行情四周期 Grade-A YOLO 排序图审（2026-09-03） | 按 Owner 本轮要求，冻结 OKX 当时全部合资格 USDT 永续合约，使用同一份 Grade-A full40 |
| 2026-09-03 | [`p1_pine_v12f_grade_a_yolo_backtest_20260903.md`](p1_pine_v12f_grade_a_yolo_backtest_20260903.md) | P1：ETH 15m Pine V12F × Grade-A YOLO 延迟融合回测（2026-09-03） | 主方案失败。** 在 `2025-01-01` 至 `2026-03-01`（右开）的 ETH-USDT-SWAP 15m |
| 2026-09-03 | [`p1_pine_v12f_grade_a_yolo_fusion_20260903.md`](p1_pine_v12f_grade_a_yolo_fusion_20260903.md) | P1：ETH 15m Pine V12F × Grade-A YOLO 区间融合审计（2026-09-03） | Pine V12F 与当前 Grade-A YOLO 已完成**研究态接线**，匹配不要求精确落在同一根 K 线： |
| 2026-09-02 | [`p1_15m_ashare_grade_a_yolo_latest_20260902.md`](p1_15m_ashare_grade_a_yolo_latest_20260902.md) | P1：最新全 A 股 15m Grade-A YOLO 跨市场扫描（2026-09-02） |  |
| 2026-09-02 | [`p1_15m_ashare_grade_a_yolo_latest_standard_retail_20260902.md`](p1_15m_ashare_grade_a_yolo_latest_standard_retail_20260902.md) | 最新全 A 股 15m 命中：普通沪深主板账户过滤版（2026-09-02） |  |
| 2026-09-02 | [`p1_15m_ma_launch_owner_yolo_causal_semantic_gate_20260902.md`](p1_15m_ma_launch_owner_yolo_causal_semantic_gate_20260902.md) | P1：YOLO 提案 + 因果语义门配对验证（2026-09-02） | 模型没有白训练，但它不再有资格单独作最终裁决。本轮把 1280 full40 YOLO 保留为**位置与方向提案层**，在其后增加一层完全确定的数值语义复核：六均线是否仍密集、K 线是否仍贴近均线、核心与启动方向是否一致，以及检测窗口里已经可见的 post 确认是否达标。 |
| 2026-09-02 | [`p1_15m_six_ma_smoothness_visibility_audit_20260902.md`](p1_15m_six_ma_smoothness_visibility_audit_20260902.md) | 15m 六均线平滑度与像素可见度审计（2026-09-02） | 1. **YOLO 图像层早已不是“双均线”，而是六条线**：`close` 上的 |
| 2026-09-02 | [`p1_4h_ma_launch_yolo_halfmonth_owner_rejection_20260902.md`](p1_4h_ma_launch_yolo_halfmonth_owner_rejection_20260902.md) | P1：4h YOLO 半月语义门 Owner 终审否决（2026-09-02） | Owner 看完第 7 次 4h holdout 交付的 34 张完整未来 K 线图后，给出批次裁决：**“都不太行”**。 |
| 2026-09-02 | [`p1_4h_ma_launch_yolo_halfmonth_semantic_gate_20260902.md`](p1_4h_ma_launch_yolo_halfmonth_semantic_gate_20260902.md) | P1：4h YOLO 最近半个月因果语义门复扫（2026-09-02） | Owner 明确回复“批准”后，本轮按同一 Grade-A full40 native-1280 checkpoint 记录为 |
| 2026-09-02 | [`p1_ashare_grade_a_yolo_1h4h_long_sina_20260902.md`](p1_ashare_grade_a_yolo_1h4h_long_sina_20260902.md) | A 股普通主板 1h / 会话 4h 多头扫描（2026-09-02） | 截至 **2026-09-02 15:00 CST** 的已完成行情，本轮在普通账户可搜索的沪深主板池中交付 |
| 2026-09-02 | [`p1_ashare_grade_a_yolo_1h4h_long_source_preflight_failure_20260902.md`](p1_ashare_grade_a_yolo_1h4h_long_source_preflight_failure_20260902.md) | A 股 1h / 会话 4h 多头扫描：数据源预检失败（2026-09-02） | 本轮**没有产生信号结果，也不能解释为“零多头”**。冻结的 Eastmoney 60m 接口虽然接受 |
| 2026-09-02 | [`p3_15m_ma_launch_l2_feature_addition_20260902.md`](p3_15m_ma_launch_l2_feature_addition_20260902.md) | 15m L2 因果特征增量实验（2026-09-02） | 最终裁决：**REJECT**。LONG tune 入选 **full_110**，SHORT tune 入选 **plus_ma_family**。本轮只允许在旧 28 列上增加特征；候选、标签、时间切分、模型参数、成本和匹配对照均保持不变。 |
| 2026-09-02 | [`p3_15m_ma_launch_l2_feature_group_ablation_20260902.md`](p3_15m_ma_launch_l2_feature_group_ablation_20260902.md) | 15m L2 28 特征分组消融（2026-09-02） | 本轮最终裁决：**REJECT**。LONG 在 tune 期选中 **ma_plus_trend_volume_volatility**，SHORT 选中 **ma_plus_trend**。28 个特征不是先验真理，而是旧基线；当前 YOLO 候选上是否应该保留，必须由时间外经济结果决定。 |
| 2026-09-02 | [`p3_15m_ma_launch_l2_reference_augmentation_20260902.md`](p3_15m_ma_launch_l2_reference_augmentation_20260902.md) | 15m L2 历史参考事件扩充实验（2026-09-02） | 本轮裁决：**REJECT_REFERENCE_AUGMENTATION**。这次真正把旧 10,000 正图与 10,000 匹配负图的事件血缘接回 K 线；原形态正负标签没有冒充盈亏，而是逐事件重新计算固定 TP5/SL2/72 收益。参考事件只加入训练，真实 L1 的 tune 与 fi... |
| 2026-09-01 | [`p0_kronos_architecture_transfer_audit_20260901.md`](p0_kronos_architecture_transfer_audit_20260901.md) | P0 Kronos 架构、证据与可迁移性审计（2026-09-01） | Kronos 值得借鉴，但现在不应接入训练、实盘或 ACTIVE。** 本轮最有价值的 |
| 2026-09-01 | [`p1_15m_arbusdt_screenshot_model_probe_20260901.md`](p1_15m_arbusdt_screenshot_model_probe_20260901.md) | ARBUSDT 截图：15m Grade-A 模型单样本检测回放（2026-09-01） | 检测出来了。** 使用最新完成的 Grade-A 8,000 正样本 + 24,000 负样本、full40、原生 |
| 2026-09-01 | [`p1_15m_ma_launch_five_model_alluniverse_20260831.md`](p1_15m_ma_launch_five_model_alluniverse_20260831.md) | 五个 15m 均线密集检测模型：近三天全币种冻结对照（2026-09-01） | 已在**同一份冻结的 OKX USDT 永续快照**上运行五个讨论过的 YOLO 权重。范围是 2026-08-28, 2026-08-29, 2026-08-30 UTC 三个完整日的全部可用 current-live crypto USDT-SWAP：**274 个币、822 个币日、每币... |
| 2026-09-01 | [`p1_4h_ma_launch_yolo_alluniverse_20260901.md`](p1_4h_ma_launch_yolo_alluniverse_20260901.md) | 4h 最新行情 YOLO 全币种扫描（2026-09-01） | 按 Owner 的“扫描所有的币种”指令，本轮在模型推理前冻结了 OKX 当时全部合资格的 |
| 2026-09-01 | [`p1_4h_ma_launch_yolo_halfmonth_20260901.md`](p1_4h_ma_launch_yolo_halfmonth_20260901.md) | 4h YOLO 全币种最近半个月检测实验（2026-09-01） | 按 Owner 要求，本轮在刚冻结的 OKX 全量合资格 USDT 永续宇宙上，把观察区间从最近 6 个已确认 |
| 2026-09-01 | [`p1_4h_ma_launch_yolo_latest_20260901.md`](p1_4h_ma_launch_yolo_latest_20260901.md) | 4h 最新行情 YOLO 扫描（2026-09-01） | 按 Owner 明确要求，本轮把现有 **Grade-A 8k 正例 + 24k 负例、full40、native-1280** YOLO |
| 2026-09-01 | [`p1_spike_eth3m_september_trade_audit_20260914.md`](p1_spike_eth3m_september_trade_audit_20260914.md) | ETHUSDT.P 3 分钟 SPIKE V8：2026-09-01 至 09-14 逐笔核对 |  |
| 2026-09-01 | [`p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.md`](p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.md) | 15m 均线密集启动：因果 L1.5 + 多空 L2 全链路审计（2026-09-01） | 本轮把用户要求的路径完整做成四组冻结对照：原 L1、L1+全局形态 L1.5、L1+多空分开收益 L2、 |
| 2026-09-01 | [`p3_15m_ma_launch_l2_short_window_side_split_20260901.md`](p3_15m_ma_launch_l2_short_window_side_split_20260901.md) | 15m 均线密集启动：精确短窗 L2 多空回归审计（2026-09-01） | 本轮把 L2 严格改成与 L1 相同的 **18/19 根、1280×742 原始输入**，只使用图里可见的 OHLC、 |
| 2026-08-31 | [`p0_5m_ma_launch_causal_rebuild_20260831.md`](p0_5m_ma_launch_causal_rebuild_20260831.md) | P0：5m MA Launch 因果重建与全量审计（2026-08-31） | 今天训练用的 5m 原始 K 线没有证据表明损坏；有问题的是**训练输入、标签时间线和评估单位的契约**。 |
| 2026-08-30 | [`p1_15m_ma_launch_owner_grade_a8000_hot3d_1280_20260830.md`](p1_15m_ma_launch_owner_grade_a8000_hot3d_1280_20260830.md) | 同一热门币快照：1280 全量训练模型与 960 模型的冻结对照（2026-08-30） | 在**完全相同**的 60 个「日期×币种」15m 行情快照、相同 W18/W19 窗口、`conf=0.25`、NMS IoU `0.70`、核心 4/5 根、确认 2–9 根、原框映射及同币重叠 episode 合并规则下，刚完成的 **YOLO11s full40 `imgsz=1280... |
| 2026-08-30 | [`p1_ma_launch_label_leakage_and_edge_20260830.md`](p1_ma_launch_label_leakage_and_edge_20260830.md) | MA 密集启动：标签泄漏与真实 edge（2026-08-30） | mAP 0.91 的那个检测器，学的不是形态，是「价格最后两根有没有动」。 |
| 2026-08-29 | [`p1_15m_ma_launch_owner_grade_a8000_960_epoch6_diagnosis_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_960_epoch6_diagnosis_20260829.md) | 960 模型为何第 6 轮成为最佳（2026-08-29） | 960 并非只训练了 6 轮。旧运行在第 16 轮触发 `patience=10` 早停；关闭早停后的对照运行完整 |
| 2026-08-29 | [`p1_15m_ma_launch_owner_grade_a8000_eth30d_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_eth30d_20260829.md) | ETHUSDT.P 近 30 日 Grade-A epoch-6 模型扫描（2026-08-29） | 本轮使用的“刚刚已经跑完的模型”是 **Grade-A 8,000 正样本 + 24,000 负样本、YOLO11s、 |
| 2026-08-29 | [`p1_15m_ma_launch_owner_grade_a8000_hot3d_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_hot3d_20260829.md) | 近 3 个完整日热门币 Grade-A 模型扫描（2026-08-29） | 本轮按此前“热门币”实际口径执行：在 OKX 当前可交易、`instCategory=1` 的加密 |
| 2026-08-28 | [`p0_15m_ma_launch_owner_perfect_filter10000_20260828.md`](p0_15m_ma_launch_owner_perfect_filter10000_20260828.md) | P0：15m 正样本 10,000 张“完美形态”严格二次过滤（2026-08-28） | 已对现有 **10,000 张 weak-positive 正样本**完成一次不凑数的严格二次过滤： |
| 2026-08-28 | [`p1_15m_ma_launch_owner_grade_a8000_20260828.md`](p1_15m_ma_launch_owner_grade_a8000_20260828.md) | P1：15m 均线密集 A 级正样本扩容到 8,000 张（2026-08-28） | 已按 Owner 的要求，不再只从原 10,000 张里挑，而是扩展历史数据重新筛选并完成一套 **8,000 张 A 级正样本数据集**： |
| 2026-08-28 | [`p1_15m_ma_launch_owner_yolo_eth30d_20260828.md`](p1_15m_ma_launch_owner_yolo_eth30d_20260828.md) | ETHUSDT.P 近 30 日原 Owner-YOLO 扫描（2026-08-28） |  |
| 2026-08-28 | [`p1_15m_ma_launch_owner_yolo_eth30d_confidence_20260828.md`](p1_15m_ma_launch_owner_yolo_eth30d_confidence_20260828.md) | ETHUSDT.P 近 30 日模型置信度分析（2026-08-28） |  |
| 2026-08-28 | [`p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.md`](p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.md) | 15m Owner-YOLO 检测框与实际训练图语义对照（2026-08-28） |  |
| 2026-08-27 | [`p0_15m_ma_launch_owner_autofill10000_20260827.md`](p0_15m_ma_launch_owner_autofill10000_20260827.md) | P0：15m 严格均线密集启动 10,000 张自动样例包（2026-08-27） | 已完成 **10,000 张**严格 15 分钟形态图，不是只能生成 50 张。最终为 **5,000 LONG + 5,000 SHORT**，每张都是 **1280×742 原始 PNG、恰好一个红框、框宽 4 或 5 根 K**；10,000 个事件、图片 SHA 和框身份均唯一。完整 ... |
| 2026-08-27 | [`p0_15m_ma_launch_owner_autofill50_20260827.md`](p0_15m_ma_launch_owner_autofill50_20260827.md) | P0：15m 均线密集启动严格自动补齐 50（2026-08-27） | 此前交付“20 张有框 + 30 张无框，再请 Owner 人工审核”是任务类型错误，现已作废。Owner 要的是合格样例包，不是固定 50 个身份的标签审计。 |
| 2026-08-27 | [`p1_15m_ma_launch_negative_parity_hardval_20260827.md`](p1_15m_ma_launch_negative_parity_hardval_20260827.md) | P1：15m 负样本两项问题审计与 hard-val 补集（2026-08-27） | 负样本**没有**首批审核图的 `t-3` 竖线错位问题；26,874 张原负样本全部没有竖线、框、 |
| 2026-08-27 | [`p1_15m_ma_launch_owner_yolo_20260827_fullcontext_analysis_20260828.md`](p1_15m_ma_launch_owner_yolo_20260827_fullcontext_analysis_20260828.md) | 2026-08-27 全部信号：高清全景复核与详细数据分析 | 已把 2026-08-27 榜单归属的 **43 个冻结事件**逐个重画并发到 Telegram：37 LONG、6 SHORT， |
| 2026-08-27 | [`p1_15m_ma_launch_owner_yolo_dataset10000_20260827.md`](p1_15m_ma_launch_owner_yolo_dataset10000_20260827.md) | P1 · 15m 均线密集启动 10,000 正例 + 10,000 负例 YOLO 数据集 | Owner 的“刚刚的10000张不错，弄成训练数据集，同时弄好负样本”已完成为一套**实际可被 YOLO 读取的本地数据集**： |
| 2026-08-27 | [`p1_15m_ma_launch_owner_yolo_neg30000_20260827.md`](p1_15m_ma_launch_owner_yolo_neg30000_20260827.md) | P1 · 15m 均线密集启动 10,000 正例 + 30,000 负例 YOLO 数据集 | Owner 的“对啊，那你应该搞 3w 张啊”已完成为一套新的、未覆盖 v1 的本地 YOLO 数据集： |
| 2026-08-27 | [`p1_15m_ma_launch_transition_box_review50_20260827.md`](p1_15m_ma_launch_transition_box_review50_20260827.md) | P1：15m 启动源区两段式框 Review50（2026-08-27） | Owner 的三张红框证明，正确目标不是上一版的“六均线细包络”，也不是“把横向范围内全部 K 线做外接矩形”。红框表达的是一个**两段式对象**：启动前核心区决定纵向价格带，随后几根启动确认 K 只延长横向证据；确认大 K 可以从框顶/框底穿出。 |
| 2026-08-25 | [`p0_btc_4h_causal_pine_v1_20260825.md`](p0_btc_4h_causal_pine_v1_20260825.md) | BTC 4h 双均线密集启动：因果 Pine V1（2026-08-25） | 已经写成一版可直接加载的 Pine v6 指标，并在 TradingView 官方编辑器完成真实编译：**0 个编译错误，编辑器源码与仓库文件 SHA-256 完全一致**。脚本已保存为私有脚本 `Fable 4H MA Launch Causal V1 · Research Only`，并添... |
| 2026-08-24 | [`p1_owner_long_candidate_manifest_20260824.md`](p1_owner_long_candidate_manifest_20260824.md) | P1 Owner-long 待审核候选 manifest | 做多数据的第一层谱系已经冻结，但**还不是可训练数据集**。 |
| 2026-08-24 | [`p1_owner_long_candidate_manifest_v2_20260824.md`](p1_owner_long_candidate_manifest_v2_20260824.md) | P1 Owner-long 待审核候选 manifest v2 | 做多数据集已经从“来源混杂”推进到一份可复核、不可覆盖的 **v2 待审核账本**，但它仍然不是训练集。 |
| 2026-08-23 | [`p1_15m_ma_launch_owner_yolo_recent5d_20260828.md`](p1_15m_ma_launch_owner_yolo_recent5d_20260828.md) | 新 Owner YOLO：最近五个完整 UTC 日 Top20 扫描报告 | 已按预注册范围扫描 **2026-08-23 至 2026-08-27** 五个完整 UTC 日；当前未收盘的 |
| 2026-08-23 | [`p1_15m_ma_launch_t3_daily_movers3d_20260826.md`](p1_15m_ma_launch_t3_daily_movers3d_20260826.md) | 最近三天每日绝对涨跌幅 Top20：15m t-3 模型扫描报告 | 已按 **UTC 完整日**扫描 2026-08-23、08-24、08-25；08-26 尚未收盘，因此没有混入。 |
| 2026-08-22 | [`p0_pine_eth_15m_v12f_compile_venue_lock_20260822.md`](p0_pine_eth_15m_v12f_compile_venue_lock_20260822.md) | ETH 15m Pine V12F：官方编译与 Venue 锁定（2026-08-22） | 1. **冻结 V12F 已在 TradingView 官方 Pine 编译器通过。** 实际页面是 |
| 2026-08-21 | [`p0_pine_eth_15m_cross_tbsl_optimization_20260821.md`](p0_pine_eth_15m_cross_tbsl_optimization_20260821.md) | ETH 15m Pine：六均线交叉因子与趋势 TP/SL 优化 | 1. **兼顾收益与回撤的当前研究候选是六线 W8 gate，不是 TBSL。** 它要求最近 8 根内， |
| 2026-08-21 | [`p0_pine_eth_15m_dense_start_release_20260821.md`](p0_pine_eth_15m_dense_start_release_20260821.md) | ETH 15m Pine：六线密集启动 V13 / 真实释放 V14 优化报告（2026-08-21） | 1. **当前没有通过统计与 holdout 门的 Pine。** V12F 只保留为冻结历史 comparator；V13/V14 均 |
| 2026-08-21 | [`p0_pine_eth_15m_forward_lr_contract_20260821.md`](p0_pine_eth_15m_forward_lr_contract_20260821.md) | ETH 15m Pine：Forward V2 与状态感知 LR 合同（2026-08-21） | 现在正确的处理不是继续拿已消费的最近半年反复调参，而是把下一阶段的入口锁死： |
| 2026-08-21 | [`p0_pine_eth_15m_next_action_20260821.md`](p0_pine_eth_15m_next_action_20260821.md) | ETH 15m Pine：V12F 失败后怎么办（2026-08-21） |  |
| 2026-08-21 | [`p0_pine_eth_15m_path_efficiency_20260821.md`](p0_pine_eth_15m_path_efficiency_20260821.md) | ETH 15m Pine：交叉前路径效率单变量审计（2026-08-21） | 固定的 `pre_cross_path_efficiency_32` **不能提高当前 Pine 信号质量，单特征假设拒绝**。在 2023–2024 冻结 V9 动态账本的 166 笔交易上，它与成本后收益的 Spearman 为 `-0.0047`（双侧 `p=0.9518`），盈利分类 ... |
| 2026-08-21 | [`p0_pine_eth_15m_start_label_audit_20260821.md`](p0_pine_eth_15m_start_label_audit_20260821.md) | ETH 15m Pine：335 候选自动先达标签与判断门优化审计 | 1. **335 个候选已经全部自动标完，不需要人工审核。** 184 个正例、151 个负例，正例率 |
| 2026-08-21 | [`p0_pine_eth_15m_trend_ensemble_20260821.md`](p0_pine_eth_15m_trend_ensemble_20260821.md) | ETH 15m Pine V15E：多速度趋势组合 + 六线软判断回测（2026-08-21） |  |
| 2026-08-21 | [`p0_pine_eth_15m_v12_preholdout_20260821.md`](p0_pine_eth_15m_v12_preholdout_20260821.md) | ETH 15m Pine V12 优化与最近半年回测前置报告（2026-08-21） |  |
| 2026-08-21 | [`p0_pine_eth_15m_v12f_holdout1_recent6m_20260821.md`](p0_pine_eth_15m_v12f_holdout1_recent6m_20260821.md) | ETH 15m Pine V12F：最近半年 holdout 第 1 次正式验收（2026-08-21） | V12F 可以继续作为**唯一的 15m paper 候选**，因为它在 preholdout 和本次受保护段都相对 V9 |
| 2026-08-21 | [`p0_pine_eth_15m_v1_20260821.md`](p0_pine_eth_15m_v1_20260821.md) | ETHUSDT.P / ETH-USDT-SWAP 15m Pine 定型与回测审计（V1） | 15 分钟已经定死为本轮唯一研究周期：本地数据契约是 **OKX `ETH-USDT-SWAP` 15m**， |
| 2026-08-21 | [`p1_fixed_w10_canonical_ohlc_triage_v2_20260821.md`](p1_fixed_w10_canonical_ohlc_triage_v2_20260821.md) | P1 统一原始 OHLC 全量筛选包 v2（2026-08-21） | Owner 指出“这些图完全不统一”是正确的。上一版 |
| 2026-08-21 | [`p1_fixed_w10_original_source_triage_20260821.md`](p1_fixed_w10_original_source_triage_20260821.md) | P1 fixed-W10 原始来源图全量筛选包（2026-08-21） | Owner 指出得对：此前 448 项盲审包展示的是统一迁移后的 W10 图，不是原始视觉证据。 |
| 2026-08-21 | [`p1_ma_rope_prefilter_20260821.md`](p1_ma_rope_prefilter_20260821.md) | P1 · 六均线“拧成一股绳”代码预筛与数据扩充入口（2026-08-21） | 代码预筛已经完成，并生成了两个可操作入口： |
| 2026-08-21 | [`p1_owner_long_dataset_lineage_audit_20260821.md`](p1_owner_long_dataset_lineage_audit_20260821.md) | P1 Owner-long 做多检测器数据谱系与镜像方案审计 | 1. **BTC 与 ETH 都是 46 个事件只是去重后的计数巧合，不是每币限额。** BTC 原始检测 |
| 2026-08-21 | [`p1_owner_short_positive_refilter_20260821.md`](p1_owner_short_positive_refilter_20260821.md) | P1 Owner 旧训练正例原图精筛包（2026-08-21） | Owner 对当前页面的质疑完全成立：目标不是核对 fixed-W10 的 2,649 行数据谱系，而是重新筛选 |
| 2026-08-21 | [`p2_owner_short_recent15d_core10_comparison_20260821.md`](p2_owner_short_recent15d_core10_comparison_20260821.md) | 两个Owner空头YOLO最近15天核心10币描述性扫描（2026-08-21） | 固定同一份Core-10快照、W12–19、conf=0.25、IoU=0.70与5根事件去重后，baseline产生 **1,005** 个事件，hard-negative R1产生 **437** 个事件。 |
| 2026-08-20 | [`p0_pine_allin_v7_preholdout_20260820.md`](p0_pine_allin_v7_preholdout_20260820.md) | P0 — Pine ALLIN-V7.2 优化与预留验证回放（2026-08-20） |  |
| 2026-08-20 | [`p1_fixed_w10_blind_audit_pack_20260820.md`](p1_fixed_w10_blind_audit_pack_20260820.md) | P1 fixed-W10 门禁修复、artifact 谱系与盲审包（2026-08-20） | 工程交付完成，标签验收仍待 Owner 盲审。** 已修复旧 acceptance 把迁移前 |
| 2026-08-20 | [`p1_gold_label_quality_20260820.md`](p1_gold_label_quality_20260820.md) | P1 — 固定 W10 金标的标签错误率（2026-08-20） |  |
| 2026-08-20 | [`p_model_inventory_20260820.md`](p_model_inventory_20260820.md) | 模型清单 — 我们到底训出了什么（2026-08-20） |  |
| 2026-08-20 | [`p_oss_framework_survey_20260820.md`](p_oss_framework_survey_20260820.md) | 开源 AI 框架评估 —— 哪些真能帮到这个项目（2026-08-20） | 库 \| 治什么 \| 裁决 \| |
| 2026-08-18 | [`p_volatility_axes_20260818.md`](p_volatility_axes_20260818.md) | 波动率两条轴：owner「币波动越高越好」假说的匹配对照检验 — 2026-08-18 |  |
| 2026-08-12 | [`p2_local_signal_v2_early_frontier_review300_owner_result_20260812.md`](p2_local_signal_v2_early_frontier_review300_owner_result_20260812.md) | Local Signal V2 — 早期启动前沿 300 张 Owner 审核结果 | 1. Owner 完成 300/300 裁决：**119 YES、181 NO、0 SKIP、0 改判**，与 manifest 的 300 个 |
| 2026-08-12 | [`p2_local_signal_v2_early_frontier_review300_prereview_20260812.md`](p2_local_signal_v2_early_frontier_review300_prereview_20260812.md) | Local Signal V2 — 早期启动前沿 300 张 Owner 审核包 PRE-REVIEW | 本轮已从两个冻结的 R1 pre-holdout 候选池中，精确排除此前四个审核包已经展示过的 |
| 2026-08-12 | [`p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md`](p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md) | Local Signal V2 Positive 语义纯度审计 PRE-REVIEW（2026-08-12） | 本轮 `DATA / SEMANTIC AUDIT ONLY` 的200张 Owner YES / NO / SKIP审核包已经完成，等待Owner人工审核。 |
| 2026-08-12 | [`p2_local_signal_v2_positive_semantic_audit_prereview_v2_20260812.md`](p2_local_signal_v2_positive_semantic_audit_prereview_v2_20260812.md) | Local Signal V2 Positive 语义审核 PRE-REVIEW v2（2026-08-12） | Owner指出v1审核包“没有走势对照，且K线像水平线”后，问题已定位并修正。当前正式入口已切换为v2；v1保留为历史构建证据，但不再用于Owner裁决。 |
| 2026-08-12 | [`p2_owner_short_gold_center_hardneg_r2_canary_20260812.md`](p2_owner_short_gold_center_hardneg_r2_canary_20260812.md) | P2 Owner确认误报第三训练臂与独立连续Canary（2026-08-12） | Owner于2026-08-11 23:14 CST授权的第三训练臂 |
| 2026-08-12 | [`p3_yoyo_dataset_v3_gold_core_prereview_20260812.md`](p3_yoyo_dataset_v3_gold_core_prereview_20260812.md) | yoyo-trading — Dataset V3 Gold Core 训练前验收 | 1 \| 旧模型哪些继续用了 \| R1 `029f80a5…` 作 R3A 初始化与全部对照基线；R2 `52cd38fd…` 只作对照；Stage A `c0e94f47…` 保留为血统起点；官方 `yolo11s.pt` `85a76fe8…` 作 R3B 冷启 \| |
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
| 2026-08-02 | [`p1_bico_194r_exit_case_20260910.md`](p1_bico_194r_exit_case_20260910.md) | BICO 194R：最高浮盈与可执行退出 |  |
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
| 2026-07-15 | [`p1_spike_eth_v9_yolo_entry_20260915.md`](p1_spike_eth_v9_yolo_entry_20260915.md) | ETH 最近两个月：V9 开仓与同级／小级别 YOLO | 不能把所有V9开仓都识别出来。** 各周期独立回放共162笔原始开仓；同级检出6笔、小级别检出10笔、两者同时检出2笔。跨周期可能是重叠市场事件，这个总数仅用于覆盖统计。 |
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
| 2026-07-10 | [`p1_altseason_multivenue_20260910.md`](p1_altseason_multivenue_20260910.md) | spike · 跨交易所山寨趋势研究：从抓到启动，到留下利润 |  |
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
| 2026-05-15 | [`p1_launch_quality_20260910.md`](p1_launch_quality_20260910.md) | spike · 1H 启动质量：过滤假启动，会不会也过滤大赢家？ | 放量和大振幅适合描述一种“强爆发启动”，本轮没有支持把它们变成所有1H箭头的必选条件。**两条过滤都在7—9月改善了组合曲线，但在较早时期大幅减少利润，也没有降低初损失败比例。因此不修改当前默认信号。 |
| 2026-05-04 | [`p1_imacd_yolo_confirmation_20260908.md`](p1_imacd_yolo_confirmation_20260908.md) | IMACD → YOLO 延迟确认试验：接线可行，盈利与去噪仍待验证 |  |
| 2026-05-04 | [`strategy_stability_preholdout.md`](strategy_stability_preholdout.md) | Pre-holdout strategy stability audit |  |
| 2026-04-28 | [`p0_15m_right_edge_screenshot_similarity_20260829.md`](p0_15m_right_edge_screenshot_similarity_20260829.md) | 15m 截图最右侧历史相似形态检索（pre-holdout） |  |
| 2026-01-01 | [`p1_spike_v9_cost_be2_20260915.md`](p1_spike_v9_cost_be2_20260915.md) | V9 加入净2R后的0.2%保护：同入场与串行回测 | 规则已实现，效果分化，不能称为统一改进。** 以下均为OKX ETH、未使用holdout；共同区间是2026-01-01至2026-05-01 UTC。 |
| 2026-01-01 | [`p2_eth_yearly_morphology_count_20260813.md`](p2_eth_yearly_morphology_count_20260813.md) | ETH 2026 同类空头形态计数（冻结门 v1） | 按本轮在扫描前冻结的 `eth_yearly_morphology_gate_v1_20260813`，2026-01-01 至 |
| 2024-09-10 | [`p1_spike_account_growth_20260913.md`](p1_spike_account_growth_20260913.md) | SPIKE 账户冻结回放技术报告 | 仓位 \| 风险 \| 路径数 \| 中位终值 \| 最好终值 \| 最差终值 \| 达 100k \| 中位已实现回撤 \| |
| 2024-08-25 | [`p0_btc_4h_ma_launch_similarity_20260825.md`](p0_btc_4h_ma_launch_similarity_20260825.md) | BTC 4h 双均线密集启动相似形态检索 |  |
| 2023-12-31 | [`p1_eth_bb_stoch_longrun_20260916.md`](p1_eth_bb_stoch_longrun_20260916.md) | ETH 5m · BB × Stoch 28 个月长周期诊断 | 把窗口从 4.4 个月拉到 28 个月（245,088 根 5m，2023-12-31 → 2026-04-30）， |
| — | [`OPEN_QUESTIONS_FOR_RESEARCH.md`](OPEN_QUESTIONS_FOR_RESEARCH.md) | 卡点与待研究问题清单(给外部调研用) |  |
| — | [`ma206_profitability_diagnosis.md`](ma206_profitability_diagnosis.md) | MA206 收益为什么弱 |  |
| — | [`ma206_q80_shadow_diagnosis.md`](ma206_q80_shadow_diagnosis.md) | MA206 q80 影子漏斗诊断 | 当前不是“只监控 50 多个币”。本地共有 `401` 个 OKX USDT SWAP 15m 文件；按既定 |
| — | [`p0_15m_ma_launch_candidate1000_20260825.md`](p0_15m_ma_launch_candidate1000_20260825.md) | 15m 双均线密集启动 1000 候选收集报告 | 已按冻结口径完成 **1000 张 15m 候选图**：LONG 500、SHORT 500，覆盖 212 个币种； |
| — | [`p0_15m_ma_launch_candidate9000_20260826.md`](p0_15m_ma_launch_candidate9000_20260826.md) | 15m 六均线密集启动新增 9000 候选与训练门报告 | 已按首批 1000 的**同一形态门和排序标准**新增 **9000 张 15m 候选图**：LONG 4500、 |
| — | [`p0_15m_ma_launch_density_core_box_review50_20260827.md`](p0_15m_ma_launch_density_core_box_review50_20260827.md) | P0：15m 六均线密集核心单框 Review50 v3 失败审计 | Owner 复审否决了 v3 的批量框语义。v3 虽然修掉了“四框”和固定 `t-3`，但仍把每个弱候选都强制当成正例：程序在 `t-12..t-1` 中选六均线全局价格包络最小的连续 5 根，没有绝对拒绝门，也不检查均线是否收敛/交织、核心是否紧邻启动、平坦平行均线是否应淘汰。 |
| — | [`p0_15m_ma_launch_owner_strict_review50_20260827.md`](p0_15m_ma_launch_owner_strict_review50_20260827.md) | P0：15m 均线密集启动严格 shortlist Review50 v5 | 这轮没有把 50 张继续强制框满。冻结 Review50 中只保留 **20 张单框提案**，其余 **30 张无框**：6 张来自 Owner 明确否决，24 张在完整联系表复核中因均线平行/过宽、价格已脱离均线、启动大 K 已进入核心或框后没有新鲜释放而严格淘汰。 |
| — | [`p0_btc_4h_ma_launch_similarity_top20_v2_failure_20260825.md`](p0_btc_4h_ma_launch_similarity_top20_v2_failure_20260825.md) | BTC 4h 相似形态 Top-20 扩展失败报告 | 本轮没有新的 4h 图可以诚实交付。获得 Owner 明确授权后，冻结的 Top-20 配置完整读取了 **54 个币、36,720 个 holdout 4h 币种行**；宽门仍有 **64 LONG / 30 SHORT**，但固定的 18 根同币同方向去重后，SHORT 最终只有 **15... |
| — | [`p0_eth4h_exit_exploration_20260907.md`](p0_eth4h_exit_exploration_20260907.md) | ETH4h R2：保本与慢均线方向的有限探索 |  |
| — | [`p0_eth4h_trend_candidate_20260907.md`](p0_eth4h_trend_candidate_20260907.md) | ETH 4H Trend R1：保守仓位候选策略 | ```json |
| — | [`p0_imacd_15m_monitor_20260908.md`](p0_imacd_15m_monitor_20260908.md) | IMACD 新增 15 分钟监听：三周期已运行 |  |
| — | [`p0_imacd_ashare_daily_long_20260909.md`](p0_imacd_ashare_daily_long_20260909.md) | Spike 沪深主板日线多头：冻结参数与样本外检验 |  |
| — | [`p0_imacd_bark_monitor_20260908.md`](p0_imacd_bark_monitor_20260908.md) | IMACD 新增 Bark 信号推送 |  |
| — | [`p0_imacd_gold_multitimeframe_20260908.md`](p0_imacd_gold_multitimeframe_20260908.md) | IMACD 黄金多周期验收：两种报价、16 个运行组合 |  |
| — | [`p0_imacd_mac_monitor_20260908.md`](p0_imacd_mac_monitor_20260908.md) | IMACD · Mac 全市场监控交付记录 |  |
| — | [`p0_imacd_pane_readability_20260908.md`](p0_imacd_pane_readability_20260908.md) | IMACD V2.6：让副图启动点与文字分开 |  |
| — | [`p0_imacd_pine_indicator_20260907.md`](p0_imacd_pine_indicator_20260907.md) | IMACD 零轴密集启动 · 多周期趋势 V1 |  |
| — | [`p0_imacd_pine_lifecycle_20260908.md`](p0_imacd_pine_lifecycle_20260908.md) | IMACD V2.5：启动之后，持续看结构与风险 |  |
| — | [`p0_imacd_pine_lines_20260907.md`](p0_imacd_pine_lines_20260907.md) | IMACD 蓝橙双线默认样式 |  |
| — | [`p0_imacd_pine_style_20260907.md`](p0_imacd_pine_style_20260907.md) | IMACD 指标显示更新 1.1 |  |
| — | [`p0_imacd_style_restore_20260908.md`](p0_imacd_style_restore_20260908.md) | IMACD 样式还原：只保留关键 K 线亮色 |  |
| — | [`p0_imacd_tv_visible_monitor_20260908.md`](p0_imacd_tv_visible_monitor_20260908.md) | IMACD 监控纠正：以 TradingView 实际可见的启动标记为准 |  |
| — | [`p0_imacd_zero_axis_monitor_fix_20260908.md`](p0_imacd_zero_axis_monitor_fix_20260908.md) | IMACD 监控纠正：只推送刚离开零轴的第一根 |  |
| — | [`p0_spike_ma_drift_short_indicator_20260914.md`](p0_spike_ma_drift_short_indicator_20260914.md) | SPIKE · 均线下压预警 V1 |  |
| — | [`p0_spike_v1_plus_evidence_20260912.md`](p0_spike_v1_plus_evidence_20260912.md) | SPIKE V1 加强版：先纠正回测口径，再验证退出与过热过滤 | 可以做独立的 **V1+ 加强版**。我的建议是保留原版的近零蓄势、六均线密集和严格量价启动，优先改进多空验证、退出保护与重复风险控制。不是再把信号放宽一轮，也不能把 Notion 里的所有“规律”直接做成过滤开关。 |
| — | [`p0_spike_yolo_monitor_20260908.md`](p0_spike_yolo_monitor_20260908.md) | spike：指标启动后 YOLO 确认已接入 |  |
| — | [`p0_xauusd_system_search_20260908.md`](p0_xauusd_system_search_20260908.md) | XAUUSD：有限交易系统搜索与冻结后确认 |  |
| — | [`p1_15m_ma_launch_boundary_review9000_20260826.md`](p1_15m_ma_launch_boundary_review9000_20260826.md) | 15m 六均线启动 9000 候选逐样本类别与边界审核入口 | 9,000 个新增候选的逐样本审核入口已经完成并通过机械与真实浏览器验收。页面一次只显示一张， |
| — | [`p1_15m_ma_launch_dataset_release_gate9000_20260826.md`](p1_15m_ma_launch_dataset_release_gate9000_20260826.md) | 15m 六均线 9000 候选 P1 数据集 release 门 | P1 前置 release planner 已完成并通过回归：它把完整 Owner 审核、精确 SHORT KEEP preview 和 |
| — | [`p1_15m_ma_launch_ma_box_review50_20260827.md`](p1_15m_ma_launch_ma_box_review50_20260827.md) | 15m 六均线密集框协议 Review50：固定 W20、模型像素下限与负样本冲突审计 | 本轮已把“框住 K 线”改成一套**只由六条均线决定的候选框协议**，并完成全量数据核算与 50 张分层审核包； |
| — | [`p1_15m_ma_launch_owner_grade_a8000_neg24000_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_neg24000_20260829.md) | 15m A级正样本的匹配负样本数据质量报告 |  |
| — | [`p1_15m_ma_launch_owner_grade_a8000_neg24000_train960_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_neg24000_train960_20260829.md) | 15m Grade-A 8,000 正例 + 24,000 匹配负例 YOLO11s 960 训练报告 | RTX3060 训练已正常完成并以退出码 0 结束：请求 40 轮，实际第 16 轮触发 |
| — | [`p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.md`](p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.md) | 15m Owner 弱标签 10,000 正 + 30,000 负 YOLO 960 训练报告 | RTX 3060 训练已完成且正常退出：请求 40 轮，实际第 29 轮触发 `patience=10` 早停， |
| — | [`p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.md`](p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.md) | 最近五日 Top20：原始 YOLO 框与单 Episode 复核修正版 | 已把上一版五日图的两个展示错误修正：**不再把同一币日的多个滑窗命中全部叠在一张图上**，也 |
| — | [`p1_15m_ma_launch_review_parity_v2_20260826.md`](p1_15m_ma_launch_review_parity_v2_20260826.md) | 15m t-3 因果训练图 / 完成走势审核图一致性修复 v2 | Owner 指出的两处问题均已重做：首批 1,000 张审核图现在 **1,000/1,000** 把蓝线画在 |
| — | [`p1_15m_ma_launch_t3_actual_model_input_audit_20260827.md`](p1_15m_ma_launch_t3_actual_model_input_audit_20260827.md) | 15m t-3 YOLO：模型实际训练输入审计 | 磁盘训练源文件是 **1280×742 PNG**，文件本身没有被离线压缩、重编码或拉伸另存。 |
| — | [`p1_15m_ma_launch_t3_label_semantics_audit_20260827.md`](p1_15m_ma_launch_t3_label_semantics_audit_20260827.md) | 15m t-3 YOLO 信号框语义审计：K 线框与均线密集框错位 | Owner 的判断是对的：当前 9,938 个正样本框定位的是 **4–7 根 K 线的最高价到最低价**，不是六条均线的密集区域。 |
| — | [`p1_15m_ma_launch_t3_yolo10000_20260826.md`](p1_15m_ma_launch_t3_yolo10000_20260826.md) | 15m 六均线密集启动 t-3 弱标签数据集与 YOLO 训练报告 | 已把首批 1,000 与新增 9,000 个 15m 完成态候选合并为 **10,000 个唯一事件**，LONG / SHORT |
| — | [`p1_15m_ma_launch_t3_yolo10000_imgsz1280_20260827.md`](p1_15m_ma_launch_t3_yolo10000_imgsz1280_20260827.md) | 15m 六均线密集启动 t-3：原图宽度 imgsz=1280 重训报告 | 本轮直接使用上一轮已经验收过的 **36,812 张 1280×742 PNG** 和对应 YOLO 标签；没有重渲染、 |
| — | [`p1_btc_bb_stoch_holdout_acceptance_20260917.md`](p1_btc_bb_stoch_holdout_acceptance_20260917.md) | BTC 5m · BB × Stoch 最终验收（holdout） |  |
| — | [`p1_btc_bb_stoch_optimization_20260916.md`](p1_btc_bb_stoch_optimization_20260916.md) | BTC 5m · BB × Stoch 315 组参数搜索 | 调参在开发段把这条规则从亏变成赚，然后在复查段全部崩掉，而且比不调参更差。 |
| — | [`p1_btc_xau_bb_stoch_timeframes_20260916.md`](p1_btc_xau_bb_stoch_timeframes_20260916.md) | BB × Stoch 换市场换周期：BTC 5m/1m、XAU 1m | 规则一个字没改，直接搬到 BTC 5m、BTC 1m、XAU 1m——没有任何一组做到费用后为正。 |
| — | [`p1_btcusdtp_genuine_flow_coverage_v34_20260907.md`](p1_btcusdtp_genuine_flow_coverage_v34_20260907.md) | Genuine Flow Coverage · V34 |  |
| — | [`p1_btcusdtp_hourly_background_support_v23_20260907.md`](p1_btcusdtp_hourly_background_support_v23_20260907.md) | Entry Comparison Coverage | V23 已实际完成。原 251 个 BTCUSDT.P 小时 K1 入口全部保留，**248 个能各配到三个不重复的背景对照，覆盖率 98.80%**，超过事先规定的至少 226/251 门槛。分配共 744 个控制时间，另外三个入口因事前波动桶未知而没有配对；未知供给没有写成零，也没有删除母信号。 |
| — | [`p1_btcusdtp_hourly_breadth_change_v22_20260907.md`](p1_btcusdtp_hourly_breadth_change_v22_20260907.md) | External Rank Change |  |
| — | [`p1_btcusdtp_hourly_breadth_v21_20260906.md`](p1_btcusdtp_hourly_breadth_v21_20260906.md) | External Rank Pressure |  |
| — | [`p1_btcusdtp_hourly_cadence_v9_20260906.md`](p1_btcusdtp_hourly_cadence_v9_20260906.md) | Slower Checks, Still Losing | 2023年1月1日至2024年12月31日为重复使用的开发期，按四个半年顺序评估，不随机切分。固定旧版286笔病例请求、849个已分配随机控制和959个原始源区；283笔各有三个控制，另3笔没有合格控制，但始终保留在286笔总样本中。控制按原有同币、时间块、波动及方向等事前条件匹配，不根据新版... |
| — | [`p1_btcusdtp_hourly_classifier_economics_v30_20260907.md`](p1_btcusdtp_hourly_classifier_economics_v30_20260907.md) | Classifier Economics · V30 | 本轮已执行你批准的独立经济诊断。原 BTCUSDT.P **1h SMA40 的 251 个入口**加上 ChartPrime Trend Classifier 源码默认 **10/100 同向状态门**，保留 78 个。它们在固定 4 小时后的平均毛收益为 **+5.23bp**，减去冻结的... |
| — | [`p1_btcusdtp_hourly_classifier_support_v29_20260907.md`](p1_btcusdtp_hourly_classifier_support_v29_20260907.md) | Trend Classifier Support |  |
| — | [`p1_btcusdtp_hourly_dual_partial_v16_20260906.md`](p1_btcusdtp_hourly_dual_partial_v16_20260906.md) | BTC Hourly: Partial Profit Realization | <!-- SOURCE: v16_summary --> |
| — | [`p1_btcusdtp_hourly_failed_confirm_v18_20260906.md`](p1_btcusdtp_hourly_failed_confirm_v18_20260906.md) | Hourly Trend Exit Confirmation | 已完成V18真实回测。仅把“5分钟首次反色且利润未覆盖成本就全平”改成“相邻第二根仍反色、重新检查慢趋势和实际开盘后才全平”。全251笔平均净收益从−17.08bp改善到−16.77bp，利润因子0.572→0.592，四个半年仍全部亏损。本轮拒绝升级，盈利目标尚未达成。 |
| — | [`p1_btcusdtp_hourly_failed_launch_v17_20260906.md`](p1_btcusdtp_hourly_failed_launch_v17_20260906.md) | BTC Hourly: Fast Reversal Exits | 已实现并真实回测“未分批前，5分钟反转且开盘毛利未超过20bp就全平”。全251笔平均净收益从 **−15.19bp降到−17.08bp**，利润因子0.680降至0.572，胜率29.08%降至20.32%。四个半年仍全部亏损，V17拒绝升级；目标尚未达成。 |
| — | [`p1_btcusdtp_hourly_failed_reduce_v19_20260906.md`](p1_btcusdtp_hourly_failed_reduce_v19_20260906.md) | Confirmed Risk Reduction | V19 把“确认启动失败后全平”改为“同一时刻、同一价格只平一半，余仓跟随原慢周期”。251 笔原始 K1 机会的平均净收益由 **−16.7690bp 变为 −16.3411bp**，只改善 0.4279bp；PF 从 0.5918 升至 0.6272，仍低于 1。四个半年全部亏损。1bp=... |
| — | [`p1_btcusdtp_hourly_frozen_ma_v12_20260906.md`](p1_btcusdtp_hourly_frozen_ma_v12_20260906.md) | BTC Frozen MA Exit V12 | <!-- SOURCE: v12_summary --> |
| — | [`p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md`](p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md) | 1h 启动与跨周期退出：七轮盈利验证 |  |
| — | [`p1_btcusdtp_hourly_launch_v11_20260906.md`](p1_btcusdtp_hourly_launch_v11_20260906.md) | BTC Launch Deadline V11 | <!-- SOURCE: v11_summary --> |
| — | [`p1_btcusdtp_hourly_management_v8_20260906.md`](p1_btcusdtp_hourly_management_v8_20260906.md) | Slower Exits, Still No Edge |  |
| — | [`p1_btcusdtp_hourly_native_exit_v15_20260906.md`](p1_btcusdtp_hourly_native_exit_v15_20260906.md) | Native Exit Timing — BTC Hourly V15 |  |
| — | [`p1_btcusdtp_hourly_prior_breakout_v14_20260906.md`](p1_btcusdtp_hourly_prior_breakout_v14_20260906.md) | BTC Hourly Breakout Support | 本轮是非收益支持审计，val AUC、排序置换p、top-decile毛/净收益、胜率、PF、单特征收益基线以及同成本随机入场收益均不适用：没有新计算或读取交易结果。原匹配对照只用于检验支持分布，不替代经济对照。 |
| — | [`p1_btcusdtp_hourly_prior_colour_v13_20260906.md`](p1_btcusdtp_hourly_prior_colour_v13_20260906.md) | BTC Prior4h Colour V13 | <!-- SOURCE: v13_summary --> |
| — | [`p1_btcusdtp_hourly_structure_v20_20260906.md`](p1_btcusdtp_hourly_structure_v20_20260906.md) | Hourly Structure Gate | 本轮不采用这个结构共振，不更新 TradingView。** 在固定 BTC 1h 大实体／吞没穿均线直接入场上，加入来自 ChartPrime Market Break 源码思想的确认结构方向门：251 个原始机会留下137笔，扣20bp成本后的平均每笔收益由−16.77bp变为−17.22... |
| — | [`p1_btcusdtp_hourly_support_v10_20260906.md`](p1_btcusdtp_hourly_support_v10_20260906.md) | BTC Hourly Support Audit | <!-- SOURCE: v10_summary --> |
| — | [`p1_btcusdtp_hourly_volume_wave_economics_v32_20260907.md`](p1_btcusdtp_hourly_volume_wave_economics_v32_20260907.md) | Volume Wave Economics · V32 |  |
| — | [`p1_btcusdtp_hourly_volume_wave_support_v31_20260907.md`](p1_btcusdtp_hourly_volume_wave_support_v31_20260907.md) | Pre-K1 Volume Support · V31 | 这轮完成了一个明确的新假设：**在小时 K1 大实体/吞没贯穿均线之前，方向量已经朝入场方向改善，是否能作为一个前置筛选条件？** 原 251 个 BTCUSDT.P 小时入口保留 **100 个（39.84%）**，148 个观察到不符合，3 个因连续历史不足而未知。四个半年分别保留 **2... |
| — | [`p1_btcusdtp_hourly_vwma_background_v27_20260907.md`](p1_btcusdtp_hourly_vwma_background_v27_20260907.md) | VWMA Background Support |  |
| — | [`p1_btcusdtp_hourly_vwma_fixed_clock_v28_20260907.md`](p1_btcusdtp_hourly_vwma_fixed_clock_v28_20260907.md) | VWMA Entry Persistence |  |
| — | [`p1_btcusdtp_hourly_vwma_reference_support_v26_20260907.md`](p1_btcusdtp_hourly_vwma_reference_support_v26_20260907.md) | SMA vs VWMA Entry Support |  |
| — | [`p1_btcusdtp_k1k2_genuine_flow_alignment_v35_20260907.md`](p1_btcusdtp_k1k2_genuine_flow_alignment_v35_20260907.md) | K1/K2 Flow Clocks · V35 |  |
| — | [`p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.md`](p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.md) | K1/K2 Delayed Entry · V39 |  |
| — | [`p1_btcusdtp_owner_k1k2_genuine_flow_v36_20260907.md`](p1_btcusdtp_owner_k1k2_genuine_flow_v36_20260907.md) | K1/K2 Genuine Flow Test · V36 |  |
| — | [`p1_btcusdtp_owner_k1k2_pending_entry_v38_20260907.md`](p1_btcusdtp_owner_k1k2_pending_entry_v38_20260907.md) | K1/K2 Pending Entry Audit · V38 |  |
| — | [`p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.md`](p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.md) | K1/K2 Exit Timing Test · V37 | 结论：延后到小级别真正翻色，能救回少数趋势，但仍不是盈利策略。V37 拒绝晋级。** 同样 63 个小时 K1/K2 事件，平均每笔净收益由 −22.54bp 改善至 −12.81bp；中位数却由 −21.97bp 降至 −29.94bp。四个半年只有一个盈利，不能用总体均值改善宣布成功。 |
| — | [`p1_chartart_bbrsi_martingale_20260915.md`](p1_chartart_bbrsi_martingale_20260915.md) | ChartArt BB＋RSI v1.1：原逻辑与亏损后翻倍 |  |
| — | [`p1_chartprime_public_confluence_audit_20260906.md`](p1_chartprime_public_confluence_audit_20260906.md) | ChartPrime Confluence Audit |  |
| — | [`p1_eth_bb_stoch_backtest_20260916.md`](p1_eth_bb_stoch_backtest_20260916.md) | ETH 5m · BB × Stoch v2 回测 | 这版在本地可用历史上净亏损：75笔完整交易，净-11.02R，胜率45.33%，按R计算PF 0.70。** 没有证明优于匹配随机入场。这次测试没有调整任何策略参数，V1门禁未加入。 |
| — | [`p1_eth_bb_stoch_indicator_20260915.md`](p1_eth_bb_stoch_indicator_20260915.md) | ETH 5m · BB × Stoch 反转指标 |  |
| — | [`p1_eth_bb_stoch_optimization_20260916.md`](p1_eth_bb_stoch_optimization_20260916.md) | ETH 5min · BB × Stoch 参数搜索 |  |
| — | [`p1_eth_bb_stoch_rsi_filter_20260916.md`](p1_eth_bb_stoch_rsi_filter_20260916.md) | ETH 5m · BB × Stoch 加 Parabolic RSI 区域过滤 | 加上「只在超卖做多、超买做空」的 RSI 门后，同一段历史里过滤后仍为净亏损：58笔完整交易， |
| — | [`p1_eth_bb_stoch_strategy_20260916.md`](p1_eth_bb_stoch_strategy_20260916.md) | ETH 5m · BB × Stoch 策略 v2 |  |
| — | [`p1_eth_ma120_longest_runs_20260914.md`](p1_eth_ma120_longest_runs_20260914.md) | ETHUSDT.P：SMA120 / EMA120 同侧最长连续区间 |  |
| — | [`p1_imacd_formation_memory_20260908.md`](p1_imacd_formation_memory_20260908.md) | SPIKE · 均线形成记忆与价格位置：第二轮验证 |  |
| — | [`p1_imacd_launch_context_20260908.md`](p1_imacd_launch_context_20260908.md) | SPIKE · 启动行情降噪：形成、突破、多周期分开验证 |  |
| — | [`p1_imacd_startup_quality_20260908.md`](p1_imacd_startup_quality_20260908.md) | SPIKE · IMACD 启动质量实证 |  |
| — | [`p1_imacd_yolo_expanded_20260908.md`](p1_imacd_yolo_expanded_20260908.md) | IMACD → YOLO 扩大检查：筛选生效，等待与形态身份仍需检验 |  |
| — | [`p1_imacd_yolo_followthrough_20260908.md`](p1_imacd_yolo_followthrough_20260908.md) | IMACD + YOLO 后续走势：24根统计与完整事后图 |  |
| — | [`p1_imacd_yolo_timeframes_20260908.md`](p1_imacd_yolo_timeframes_20260908.md) | IMACD → YOLO 的1H/4H迁移：先核对确认与等待时钟 |  |
| — | [`p1_ma_shift_stoch_eth_month_20260915.md`](p1_ma_shift_stoch_eth_month_20260915.md) | ETH 近一月：15分钟颜色 × 5分钟 Stoch |  |
| — | [`p1_ma_stoch_exit_optimization_20260915.md`](p1_ma_stoch_exit_optimization_20260915.md) | ETH：退出、止盈、止损优化 v1 |  |
| — | [`p1_ma_stoch_exit_optimization_v2_20260915.md`](p1_ma_stoch_exit_optimization_v2_20260915.md) | ETH止盈止损第二轮：更宽止损与不同获利退出 |  |
| — | [`p1_mainstream_super_trend_20260910.md`](p1_mainstream_super_trend_20260910.md) | 主流币超级趋势：固定规则迁移回测 |  |
| — | [`p1_owner_eth_perfect_platform_semantic_audit_20260811.md`](p1_owner_eth_perfect_platform_semantic_audit_20260811.md) | ETH 完美平台语义审查：短延迟、多位置、不自动贴标签 | 发现 \| 证据 \| 严重度 \| 置信度 \| 裁决 \| |
| — | [`p1_owner_gold_center_crop_review_20260811.md`](p1_owner_gold_center_crop_review_20260811.md) | P1 原始空头金标中心裁切审核 | 当前61张Codex逐图目测橙框不再作为下一版标签来源。新的审核包直接联结两份Owner事实： |
| — | [`p1_owner_okx_history_20260916.md`](p1_owner_okx_history_20260916.md) | 你的交易复盘与执行系统 | 这份记录里的交易总体亏损，暂不支持“已经能够稳定盈利”。** 2024 年 2 月 23 日至 2026 年 9 月 13 日，4,898 条 USDT 持仓记录重构净损益 **-14,828.48 USDT**；另有一条 BTC 币本位记录单独列示。这里评价的是这份文件，不是你全部资产或所有账户。 |
| — | [`p1_owner_short_gold_center_dataset_20260811.md`](p1_owner_short_gold_center_dataset_20260811.md) | P1 Owner空头金标中心裁切全量数据集 | Owner确认“不要Codex重新手割；从最早金标红框中心取几根K线作为橙框”后，已将该合同扩到完整Owner-short母池。 |
| — | [`p1_spike_ashare_v1_v8_three_year_20260913.md`](p1_spike_ashare_v1_v8_three_year_20260913.md) | SPIKE V1 / V8：沪深主板近三年日线与周线 | 结论：四组单笔平均净收益均为负，且全部低于本次固定匹配随机对照；本样本不支持这四组入场规则具有正向超额。** 这是已采集样本的描述性历史结论，不能推断完整主板、共享账户或未来实盘表现。 |
| — | [`p1_spike_burst_early_warning_20260910.md`](p1_spike_burst_early_warning_20260910.md) | SPIKE V3：结构早预警与动能确认分层 |  |
| — | [`p1_spike_burst_launch_recall_20260910.md`](p1_spike_burst_launch_recall_20260910.md) | SPIKE V2：渐进启动补漏与全池召回验证 | V2 在**最多延后1根收盘确认**的主口径下，召回 10.84%（180/1660）；V1为 0.84%（14/1660）。**尚未达到80%目标。**这个分母来自全池独立价格事件，不是三张成功截图，也不是全部上涨币种。 |
| — | [`p1_spike_burst_noise_20260910.md`](p1_spike_burst_noise_20260910.md) | SPIKE：频繁信号的定位与单项降噪实测 |  |
| — | [`p1_spike_burst_three_year_20260910.md`](p1_spike_burst_three_year_20260910.md) | SPIKE 强劲爆发 V1：三年冻结规则回顾 |  |
| — | [`p1_spike_coin_be_review_20260912.md`](p1_spike_coin_be_review_20260912.md) | SPIKE 逐币退出复查：原退出 vs 1R 推保本 | 结论：逐币结果没有给出可替换 baseline 的稳定 BE 规则。** OKX 1H 最近一年里，V6 的 |
| — | [`p1_spike_eth3m_ict_sessions_20260914.md`](p1_spike_eth3m_ict_sessions_20260914.md) | ETH3m V8：只在ICT指定时段开仓 |  |
| — | [`p1_spike_eth_martingale_20260914.md`](p1_spike_eth_martingale_20260914.md) | ETH V8 止损后倍投：1000 USDT 有限资金研究 | 开发搜索共比较 40 次，涉及 34 个周期×参数组合（去掉周期后为 25 组不同参数）；候选期末余额最高 923.32U。所有开发候选期末余额是否低于 1000U：是。 |
| — | [`p1_spike_exit_policy_20260912.md`](p1_spike_exit_policy_20260912.md) | SPIKE V1／V6／V7：退出规则与账户风险比较 | 这轮没有找到可直接替换原退出的“最优系统”。** 第一年选中的6个非基线退出方案，在第二年有0个提高了同周期基线收益；因此不部署推保本、分批止盈或提前退出。保留原基线作为研究参照。 |
| — | [`p1_spike_market_breadth_20260913.md`](p1_spike_market_breadth_20260913.md) | SPIKE 市场广度：冻结候选的匹配随机对照整合报告 | 冻结的 `joint_delta_60m > 0` **应拒绝作为统一硬过滤**：6 个 cohort 的 matched 配对差值净R跨组合方向翻转，全部 6 个 paired p 均不显著（最小 p=0.2964），且至少一个 cohort 丢失了原有 ≥10R 候选。此结论只拒绝这条冻结... |
| — | [`p1_spike_noise_reduction_execution_20260913.md`](p1_spike_noise_reduction_execution_20260913.md) | SPIKE V7／V8 五条降噪路线：执行与验收报告 | 五条建议已经逐项执行。结果不是“再叠五个过滤条件”，而是把可证实的用途分开： |
| — | [`p1_spike_pepe_owner_notes_20260910.md`](p1_spike_pepe_owner_notes_20260910.md) | PEPE 4H：Owner 批注与 V4 确认含义核对 |  |
| — | [`p1_spike_v1_plus_backtest_20260912.md`](p1_spike_v1_plus_backtest_20260912.md) | SPIKE V1+ 完整默认配置：两年回测 |  |
| — | [`p1_spike_v1_triple_exit_20260914.md`](p1_spike_v1_triple_exit_20260914.md) | SPIKE V1 三规则组合退出：逐根回放 |  |
| — | [`p1_spike_v3_confirmation_gate_20260910.md`](p1_spike_v3_confirmation_gate_20260910.md) | SPIKE V3：确认之后再抑制重复预警 |  |
| — | [`p1_spike_v3_density_rearm_20260910.md`](p1_spike_v3_density_rearm_20260910.md) | SPIKE G：提示减少了，但丢掉了太多及时启动 | “同一密集阶段只报一次、重新形成密集后再报”的 G 规则，没有达到“少一半提示，同时保留九成正确启动”的目标。**同根合并标签从 12,042 降到 6,807，减少 **43.47%**；原 V3 已经及时抓到的 1,463 个正例只保留 **860 个，58.78%**，另外丢失 603 ... |
| — | [`p1_spike_v3_focus_20260910.md`](p1_spike_v3_focus_20260910.md) | SPIKE：减少提示之后，真正的启动还留住了吗 |  |
| — | [`p1_spike_v3_formation_gate_20260910.md`](p1_spike_v3_formation_gate_20260910.md) | SPIKE V3：突破前均线收拢，能否减少假启动 |  |
| — | [`p1_spike_v3_formation_gate_20260910_r2.md`](p1_spike_v3_formation_gate_20260910_r2.md) | SPIKE V3：突破前均线收拢，能否减少假启动 |  |
| — | [`p1_spike_v3_gate_diagnostic_20260910.md`](p1_spike_v3_gate_diagnostic_20260910.md) | V3 为什么信号太多，以及如何降噪 | 比较基准是原版「SPIKE强劲爆发V1」，本报告忽略中间版本。**V3早预警把“近零蓄势后强劲爆发”放宽成“突破前12根高点并站上两条快均线”；确认阶段也未恢复近零蓄势、整段箱体边界和原来的K线质量门槛。为了补漏，候选层被放宽过多，而且候选层默认带盈亏参考框，与Owner要的开仓级启动形态不一致。 |
| — | [`p1_spike_v3_htf_gate_20260910.md`](p1_spike_v3_htf_gate_20260910.md) | SPIKE V3：只用已收盘4H背景，能否减少假启动 |  |
| — | [`p1_spike_v3_layered_display_20260910.md`](p1_spike_v3_layered_display_20260910.md) | V3 分层观察：保留事件，减少反复开仓的视觉暗示 |  |
| — | [`p1_spike_v3_noise_overview_20260910.md`](p1_spike_v3_noise_overview_20260910.md) | SPIKE V3 降噪总览：原版与六次固定探索 | 现有证据支持先在呈现上把结构观察、质量升级和参考趋势生命周期明确区分：保留真实发现时点，不让每个观察都长得像新开仓。它能减少重复开仓暗示，但属于交互语义改进，不代表统计上的假信号已经减少。 |
| — | [`p1_spike_v3_price_acceptance_20260910.md`](p1_spike_v3_price_acceptance_20260910.md) | SPIKE V3：突破后等待一根，价格接受研究 | 时期 \| 按发现时钟候选 \| 通过 \| 拒绝 \| 未知 \| 按公开时钟接受 \| 跨起点流入 \| 跨终点流出 \| 父公开数量减少 \| |
| — | [`p1_spike_v3_retest_diagnostic_20260910.md`](p1_spike_v3_retest_diagnostic_20260910.md) | SPIKE：下一根回到区间内，究竟删掉了什么 |  |
| — | [`p1_spike_v4_confirmed_only_20260910.md`](p1_spike_v4_confirmed_only_20260910.md) | V4：图表只显示确认信号 |  |
| — | [`p1_spike_v4_quiet_display_20260910.md`](p1_spike_v4_quiet_display_20260910.md) | V4 简化：默认只看预警和所属确认 |  |
| — | [`p1_spike_v4_saved_20260910.md`](p1_spike_v4_saved_20260910.md) | SPIKE V4 已保存到 TradingView |  |
| — | [`p1_spike_v5_pepe_1h_repair_20260910.md`](p1_spike_v5_pepe_1h_repair_20260910.md) | PEPE 1H：V5 漏报修复与实际确认时间 |  |
| — | [`p1_spike_v5_structure_confirmation_20260910.md`](p1_spike_v5_structure_confirmation_20260910.md) | SPIKE V5：将“同一段结构完成”作为最终确认 |  |
| — | [`p1_spike_v6_bb_squeeze_20260912.md`](p1_spike_v6_bb_squeeze_20260912.md) | V6 × BB200 压缩：四周期固定组合回测 | 这个方向有局部证据，但“极致压缩”不能成为所有 V6 信号的统一硬门槛。** 最近一年 1H 的每笔交易质量明显改善，30m 两个年份都出现改善；15m 和 4H 没有稳定改善。更关键的是：提高盈利因子与保留大趋势是两件事。1H 严格组合把原来 614 笔已完成交易减至 141 笔，胜率由 2... |
| — | [`p1_spike_v7_first_launch_20260912.md`](p1_spike_v7_first_launch_20260912.md) | V7 同一段压缩的首次启动：固定对照研究 | 暂不把这两种规则加进线上V7。** 它们能减少提示和部分回撤，但没有同时做到保住大赢家、提高两个年度的收益。 |
| — | [`p1_spike_v7_v1_compare_20260912.md`](p1_spike_v7_v1_compare_20260912.md) | SPIKE V7 BB 背景准入与归档 V1：结果交付 | V7 已加入 BB 压缩背景并保存到 TradingView；全池回测完成，但本轮不能把它认定为全面优于 V1 的加强版。** 它大幅减少 V6 交易，仍比 V1 频繁很多；30 分钟、1 小时扣费后 PF 小于 1。4 小时虽有 PF 大于 1 的结果，匹配随机入场未支持其独立优势。 |
| — | [`p1_spike_v8_eth3m_be_20260913.md`](p1_spike_v8_eth3m_be_20260913.md) | V8 ETH 3分钟：浮盈触及1R后推入场价保本 | 不建议把这项1R价格保本规则直接加入当前ETH 3m V8。它确实减少部分原亏损，却损失更多原盈利；完整串行回撤的小幅下降没有抵消净收益和大趋势保留的下降。本轮不继续搜索0.95R或其他触发阈值。 |
| — | [`p1_spike_v8_eth_baseline_stats_20260914.md`](p1_spike_v8_eth_baseline_stats_20260914.md) | V8 ETH 永续 3分钟 / 5分钟：原始交易统计 |  |
| — | [`p1_spike_v8_ict_exits_20260915.md`](p1_spike_v8_ict_exits_20260915.md) | ETH15min＋ICT：止盈与利润保护的8套对照 |  |
| — | [`p1_spike_v8_ict_winner_peaks_20260915.md`](p1_spike_v8_ict_winner_peaks_20260915.md) | ETH15min＋ICT：盈利单峰值与止盈诊断 | 盈利单多数能走到约4—6R，少数走到10R以上。** 连续历史123笔中32笔最终净盈利；赢家峰值中位4.36R、均值6.09R、最大35.59R。2026前4月9笔赢家的峰值中位5.24R、最大16.36R。只看最大值会被两笔超大行情带偏。 |
| — | [`p1_spike_v8_lowtf_20260913.md`](p1_spike_v8_lowtf_20260913.md) | SPIKE V8 低周期：BTC、ETH 与因果日榜单回放 | V8 不适合原样下放到 3m 或 5m。BTC/ETH 的 5m 开发、验证段 PF 均低于 1；授权查看的 3m holdout-era 段也全部低于 1。前一日 UTC 收益 Top10 的因果山寨池没有改善这一点：284 笔已结束交易 PF **0.636**、合计 **-77.82R*... |
| — | [`p1_spike_v8_ma_cycle_20260913.md`](p1_spike_v8_ma_cycle_20260913.md) | V8：均线压缩、启动、扩散、再盘整的验证 | 这套固定状态定义能解释 COMP 三笔交易的部分区别，但**没有通过“减少噪音且保留大趋势”的硬过滤验收**。最近一年，只保留原 V8 确认时处于同向“启动中”的交易，净胜率从28.86%升至29.74%，平均净R从-0.08383改善至-0.05433，仍未转正；原319笔已实现10R只剩7... |
| — | [`p1_spike_v8_noise_filter_20260913.md`](p1_spike_v8_noise_filter_20260913.md) | SPIKE V8：V7 全量信号降噪与冻结规则回放 | V7 共 **132,593 条确认事件**，其中 **107,238 条形成串行交易**，另有 **25,355 条**因同一交易流已有持仓而没有成为新交易。冻结的 V8 门槛保留 **113,295 条确认**、形成 **95,191 条交易，信号总量减少 14.55%**。 |
| — | [`p1_spike_v8_total2_1h_native_20260914.md`](p1_spike_v8_total2_1h_native_20260914.md) | V8 × TOTAL2 1H：TradingView 原生回测 |  |
| — | [`p1_spike_v9_asset_exclusions_20260915.md`](p1_spike_v9_asset_exclusions_20260915.md) | 按币种成绩排除：V9 三个固定条件 | 完整账本保留每笔原先的同流、同方向、同时间块、同波动桶随机控制。整币删除时只保留同一实际事件原绑定的控制，没有重抽；只有两边均可评分且符合原时间边界的配对进入统计。全期事后选择也会污染筛选后的控制检验，不将其中较小的 p 值用作筛币验收。 |
| — | [`p1_trendline_v2_tbsl_20260918.md`](p1_trendline_v2_tbsl_20260918.md) | 下降趋势线突破 V2 · 止盈止损网格与多周期回测 | 三个周期全部未通过事前三条标准。没有一组止盈止损参数值得拿去消耗 holdout。 |
| — | [`p1_useless_multiscale_launch_20260909.md`](p1_useless_multiscale_launch_20260909.md) | SPIKE · USELESS 多周期启动复盘与超级趋势原型 |  |
| — | [`p1_yolo_owner_annotation_audit_20260908.md`](p1_yolo_owner_annotation_audit_20260908.md) | 你已调整的 72 张图：怎样标平台、怎样看后续 150 根 |  |
| — | [`p2_local_signal_v2_positive_semantic_audit_owner_result_20260812.md`](p2_local_signal_v2_positive_semantic_audit_owner_result_20260812.md) | Local Signal V2 语义审核结果：Positive基本成立，连续判别边界失败 |  |
| — | [`p2_local_signal_v2_semantic_boundary_diagnosis_20260812.md`](p2_local_signal_v2_semantic_boundary_diagnosis_20260812.md) | Local Signal V2：模型为什么把普通形态也认成正信号 |  |
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
| — | [`p3_15m_ma_launch_l1_l2_bypass_l15_20260901.md`](p3_15m_ma_launch_l1_l2_bypass_l15_20260901.md) | 15m 均线密集启动：移除 L1.5 后的 L1→L2 旁路验证 | L1.5 已从默认研究链中**物理旁路**。新入口只执行： |
| — | [`p3_15m_ma_launch_l2_global_context_20260901.md`](p3_15m_ma_launch_l2_global_context_20260901.md) | 15m 均线密集启动：L2 全局上下文判断层 v1 | 本轮结论是：**未通过研究门**。L1 继续只负责在 18/19 根局部图里提出候选；L2 在 L1 最后一根可见 K 线收盘后，读取最多 168 根历史形成的 28 个因果特征，再用调参段固定的 q90 分数门过滤。最终验证段没有参与训练、早停或阈值选择。 |
| — | [`p3_15m_ma_launch_l2_side_split_20260901.md`](p3_15m_ma_launch_l2_side_split_20260901.md) | 15m 均线密集启动：L2 多空拆分回归 v1 | 本轮按 Owner 要求把上一轮 **3,779 条完全相同的 L2 数据**拆成 LONG 1,801 条与 |
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
- [`p0_15m_ma_launch_candidate1000_20260825.md`](p0_15m_ma_launch_candidate1000_20260825.md) — 15m 双均线密集启动 1000 候选收集报告
- [`p0_15m_ma_launch_candidate9000_20260826.md`](p0_15m_ma_launch_candidate9000_20260826.md) — 15m 六均线密集启动新增 9000 候选与训练门报告
- [`p0_15m_ma_launch_density_core_box_review50_20260827.md`](p0_15m_ma_launch_density_core_box_review50_20260827.md) — P0：15m 六均线密集核心单框 Review50 v3 失败审计
- [`p0_15m_ma_launch_owner_autofill10000_20260827.md`](p0_15m_ma_launch_owner_autofill10000_20260827.md) — P0：15m 严格均线密集启动 10,000 张自动样例包（2026-08-27）
- [`p0_15m_ma_launch_owner_autofill50_20260827.md`](p0_15m_ma_launch_owner_autofill50_20260827.md) — P0：15m 均线密集启动严格自动补齐 50（2026-08-27）
- [`p0_15m_ma_launch_owner_perfect_filter10000_20260828.md`](p0_15m_ma_launch_owner_perfect_filter10000_20260828.md) — P0：15m 正样本 10,000 张“完美形态”严格二次过滤（2026-08-28）
- [`p0_15m_ma_launch_owner_strict_review50_20260827.md`](p0_15m_ma_launch_owner_strict_review50_20260827.md) — P0：15m 均线密集启动严格 shortlist Review50 v5
- [`p0_15m_right_edge_screenshot_similarity_20260829.md`](p0_15m_right_edge_screenshot_similarity_20260829.md) — 15m 截图最右侧历史相似形态检索（pre-holdout）
- [`p0_5m_ma_launch_causal_rebuild_20260831.md`](p0_5m_ma_launch_causal_rebuild_20260831.md) — P0：5m MA Launch 因果重建与全量审计（2026-08-31）
- [`p0_ai_strategy_factory_workflow_audit_20260913.md`](p0_ai_strategy_factory_workflow_audit_20260913.md) — AI 策略工厂能否赚钱，以及怎样接入当前 SPIKE
- [`p0_alpha_report.md`](p0_alpha_report.md) — P0 报告：人工标签是否含 alpha？
- [`p0_altcoin_rotation_system_20260909.md`](p0_altcoin_rotation_system_20260909.md) — 选择性山寨观察系统 v1：已跑通，尚未验证收益
- [`p0_baseline_audit_20260803.md`](p0_baseline_audit_20260803.md) — P0.0 基线审计 —— 仓库现状 vs Grok Build 接管计划
- [`p0_btc_4h_causal_pine_v1_20260825.md`](p0_btc_4h_causal_pine_v1_20260825.md) — BTC 4h 双均线密集启动：因果 Pine V1（2026-08-25）
- [`p0_btc_4h_ma_launch_similarity_20260825.md`](p0_btc_4h_ma_launch_similarity_20260825.md) — BTC 4h 双均线密集启动相似形态检索
- [`p0_btc_4h_ma_launch_similarity_top20_v2_failure_20260825.md`](p0_btc_4h_ma_launch_similarity_top20_v2_failure_20260825.md) — BTC 4h 相似形态 Top-20 扩展失败报告
- [`p0_codex_subagents_review_20260910.md`](p0_codex_subagents_review_20260910.md) — VoltAgent Awesome Codex Subagents：源码审阅与项目适配分析
- [`p0_comp_ma_sequence_case_20260913.md`](p0_comp_ma_sequence_case_20260913.md) — COMP 1H：密集、排列与扩散的先后顺序
- [`p0_eth4h_exit_exploration_20260907.md`](p0_eth4h_exit_exploration_20260907.md) — ETH4h R2：保本与慢均线方向的有限探索
- [`p0_eth4h_trend_candidate_20260907.md`](p0_eth4h_trend_candidate_20260907.md) — ETH 4H Trend R1：保守仓位候选策略
- [`p0_gold_ma_indicator_20260914.md`](p0_gold_ma_indicator_20260914.md) — 金标形态指标：目标纠偏与首轮回放（2026-09-14）
- [`p0_imacd_15m_monitor_20260908.md`](p0_imacd_15m_monitor_20260908.md) — IMACD 新增 15 分钟监听：三周期已运行
- [`p0_imacd_ashare_daily_long_20260909.md`](p0_imacd_ashare_daily_long_20260909.md) — Spike 沪深主板日线多头：冻结参数与样本外检验
- [`p0_imacd_bark_monitor_20260908.md`](p0_imacd_bark_monitor_20260908.md) — IMACD 新增 Bark 信号推送
- [`p0_imacd_gold_multitimeframe_20260908.md`](p0_imacd_gold_multitimeframe_20260908.md) — IMACD 黄金多周期验收：两种报价、16 个运行组合
- [`p0_imacd_ma_mtf_20260907.md`](p0_imacd_ma_mtf_20260907.md) — IMACD＋六均线密集＋多周期：找到有效改进，也找到过滤大趋势的原因
- [`p0_imacd_mac_monitor_20260908.md`](p0_imacd_mac_monitor_20260908.md) — IMACD · Mac 全市场监控交付记录
- [`p0_imacd_okx_multitimeframe_20260907.md`](p0_imacd_okx_multitimeframe_20260907.md) — Impulse MACD 34/9：OKX BTCUSDT.P / ETHUSDT.P 多周期分析
- [`p0_imacd_pane_readability_20260908.md`](p0_imacd_pane_readability_20260908.md) — IMACD V2.6：让副图启动点与文字分开
- [`p0_imacd_pine_focus_20260907.md`](p0_imacd_pine_focus_20260907.md) — IMACD 蓄势释放：长横盘与首次扩张的视觉重点
- [`p0_imacd_pine_indicator_20260907.md`](p0_imacd_pine_indicator_20260907.md) — IMACD 零轴密集启动 · 多周期趋势 V1
- [`p0_imacd_pine_lifecycle_20260908.md`](p0_imacd_pine_lifecycle_20260908.md) — IMACD V2.5：启动之后，持续看结构与风险
- [`p0_imacd_pine_lines_20260907.md`](p0_imacd_pine_lines_20260907.md) — IMACD 蓝橙双线默认样式
- [`p0_imacd_pine_retest_20260908.md`](p0_imacd_pine_retest_20260908.md) — IMACD：清晰零轴、信号价格与 SMA20 影线回踩
- [`p0_imacd_pine_style_20260907.md`](p0_imacd_pine_style_20260907.md) — IMACD 指标显示更新 1.1
- [`p0_imacd_profit_mechanism_20260907.md`](p0_imacd_profit_mechanism_20260907.md) — IMACD：零轴横盘后启动，利润怎样留在手里
- [`p0_imacd_style_restore_20260908.md`](p0_imacd_style_restore_20260908.md) — IMACD 样式还原：只保留关键 K 线亮色
- [`p0_imacd_telegram_chart_20260908.md`](p0_imacd_telegram_chart_20260908.md) — Telegram 简讯与信号图验收 · 2026-09-08
- [`p0_imacd_tv_risk_box_20260908.md`](p0_imacd_tv_risk_box_20260908.md) — TradingView IMACD 启动 K 线盈亏比框：工程验收
- [`p0_imacd_tv_visible_monitor_20260908.md`](p0_imacd_tv_visible_monitor_20260908.md) — IMACD 监控纠正：以 TradingView 实际可见的启动标记为准
- [`p0_imacd_v27_risk_reference_20260909.md`](p0_imacd_v27_risk_reference_20260909.md) — IMACD V2.7：结构止损与真实趋势空间
- [`p0_imacd_zero_axis_monitor_fix_20260908.md`](p0_imacd_zero_axis_monitor_fix_20260908.md) — IMACD 监控纠正：只推送刚离开零轴的第一根
- [`p0_independent_acceptance_20260803.md`](p0_independent_acceptance_20260803.md) — P0 独立验收报告（2026-08-03）
- [`p0_kronos_architecture_transfer_audit_20260901.md`](p0_kronos_architecture_transfer_audit_20260901.md) — P0 Kronos 架构、证据与可迁移性审计（2026-09-01）
- [`p0_local_signal_v2_audit_20260807.md`](p0_local_signal_v2_audit_20260807.md) — P0 — 局部信号 V2 交接规范：旧管线审计、基线冻结与因果门测量
- [`p0_local_signal_v2_stagea_randomcrop_v1_report_20260811.md`](p0_local_signal_v2_stagea_randomcrop_v1_report_20260811.md) — Local Signal V2 Stage A 真实裁剪 P0 报告（2026-08-11）
- [`p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md`](p0_local_signal_v2_stageb_from_stagea_v1_report_20260811.md) — Local Signal V2 Stage B-from-A 数据验收报告（2026-08-11）
- [`p0_local_signal_v2_stageb_report.md`](p0_local_signal_v2_stageb_report.md) — P0 — Local Signal V2 Stage B：因果数据集重建与硬门槛通过
- [`p0_local_signal_v2_stageb_strictneg_v2_report.md`](p0_local_signal_v2_stageb_strictneg_v2_report.md) — P0 修复 — Local Signal V2 Stage B strict-negative V2
- [`p0_pine_allin_eth4h_20260907.md`](p0_pine_allin_eth4h_20260907.md) — ALLIN V7：ETHUSDT 永续 4H 原码回放
- [`p0_pine_allin_v7_preholdout_20260820.md`](p0_pine_allin_v7_preholdout_20260820.md) — P0 — Pine ALLIN-V7.2 优化与预留验证回放（2026-08-20）
- [`p0_pine_eth_15m_cross_tbsl_optimization_20260821.md`](p0_pine_eth_15m_cross_tbsl_optimization_20260821.md) — ETH 15m Pine：六均线交叉因子与趋势 TP/SL 优化
- [`p0_pine_eth_15m_dense_start_release_20260821.md`](p0_pine_eth_15m_dense_start_release_20260821.md) — ETH 15m Pine：六线密集启动 V13 / 真实释放 V14 优化报告（2026-08-21）
- [`p0_pine_eth_15m_forward_lr_contract_20260821.md`](p0_pine_eth_15m_forward_lr_contract_20260821.md) — ETH 15m Pine：Forward V2 与状态感知 LR 合同（2026-08-21）
- [`p0_pine_eth_15m_next_action_20260821.md`](p0_pine_eth_15m_next_action_20260821.md) — ETH 15m Pine：V12F 失败后怎么办（2026-08-21）
- [`p0_pine_eth_15m_path_efficiency_20260821.md`](p0_pine_eth_15m_path_efficiency_20260821.md) — ETH 15m Pine：交叉前路径效率单变量审计（2026-08-21）
- [`p0_pine_eth_15m_start_label_audit_20260821.md`](p0_pine_eth_15m_start_label_audit_20260821.md) — ETH 15m Pine：335 候选自动先达标签与判断门优化审计
- [`p0_pine_eth_15m_trend_ensemble_20260821.md`](p0_pine_eth_15m_trend_ensemble_20260821.md) — ETH 15m Pine V15E：多速度趋势组合 + 六线软判断回测（2026-08-21）
- [`p0_pine_eth_15m_v12_preholdout_20260821.md`](p0_pine_eth_15m_v12_preholdout_20260821.md) — ETH 15m Pine V12 优化与最近半年回测前置报告（2026-08-21）
- [`p0_pine_eth_15m_v12f_compile_venue_lock_20260822.md`](p0_pine_eth_15m_v12f_compile_venue_lock_20260822.md) — ETH 15m Pine V12F：官方编译与 Venue 锁定（2026-08-22）
- [`p0_pine_eth_15m_v12f_holdout1_recent6m_20260821.md`](p0_pine_eth_15m_v12f_holdout1_recent6m_20260821.md) — ETH 15m Pine V12F：最近半年 holdout 第 1 次正式验收（2026-08-21）
- [`p0_pine_eth_15m_v1_20260821.md`](p0_pine_eth_15m_v1_20260821.md) — ETHUSDT.P / ETH-USDT-SWAP 15m Pine 定型与回测审计（V1）
- [`p0_runtime_parity_audit_20260803.md`](p0_runtime_parity_audit_20260803.md) — P0 Runtime Parity 审计（2026-08-03）
- [`p0_safety_protocol_repair_20260803.md`](p0_safety_protocol_repair_20260803.md) — P0-SAFETY short 协议修复报告（2026-08-03）
- [`p0_spike_burst_indicator_20260910.md`](p0_spike_burst_indicator_20260910.md) — SPIKE 强劲爆发 V1：独立指标工程验收
- [`p0_spike_burst_risk_display_20260910.md`](p0_spike_burst_risk_display_20260910.md) — SPIKE 强劲爆发 V1：盈亏框恢复验收
- [`p0_spike_gainers_audit_20260909.md`](p0_spike_gainers_audit_20260909.md) — Spike 涨幅榜信号核对 · 2026-09-09
- [`p0_spike_ma_drift_short_indicator_20260914.md`](p0_spike_ma_drift_short_indicator_20260914.md) — SPIKE · 均线下压预警 V1
- [`p0_spike_v1_monitor_migration_20260911.md`](p0_spike_v1_monitor_migration_20260911.md) — SPIKE V1 Mac monitor migration：信号浏览、冻结回放图与服务验收记录
- [`p0_spike_v1_okx_133_review_20260911.md`](p0_spike_v1_okx_133_review_20260911.md) — SPIKE Burst V1 · OKX 133 笔逐笔图册（2026-09-11）
- [`p0_spike_v1_plus_evidence_20260912.md`](p0_spike_v1_plus_evidence_20260912.md) — SPIKE V1 加强版：先纠正回测口径，再验证退出与过热过滤
- [`p0_spike_v1_plus_implementation_20260912.md`](p0_spike_v1_plus_implementation_20260912.md) — SPIKE 强劲爆发 V1+：保护与因果参考实现（2026-09-12）
- [`p0_spike_v9_card_performance_20260917.md`](p0_spike_v9_card_performance_20260917.md) — V9 信号卡片接上持仓跟踪：R 与胜率不再是破折号
- [`p0_spike_yolo_monitor_20260908.md`](p0_spike_yolo_monitor_20260908.md) — spike：指标启动后 YOLO 确认已接入
- [`p0_tv_release_4h_logic_20260914.md`](p0_tv_release_4h_logic_20260914.md) — release-20260201-回放样式：ETH 4H 逻辑与回测口径核对
- [`p0_two_key_candle_ma_retest_deep_dive_20260904.md`](p0_two_key_candle_ma_retest_deep_dive_20260904.md) — P0 — 两根关键 K 线 + SMA40 回踩：55 维因果拆解与盈利性证伪（2026-09-04）
- [`p0_two_key_candle_sma40_pine_indicator_20260904.md`](p0_two_key_candle_sma40_pine_indicator_20260904.md) — P0 — 双关键 K 线 + SMA40 回踩 Pine v6 指标交付（2026-09-04）
- [`p0_xauusd_system_search_20260908.md`](p0_xauusd_system_search_20260908.md) — XAUUSD：有限交易系统搜索与冻结后确认
- [`p15_h10_short_report.md`](p15_h10_short_report.md) — P1.5 R2：H10 做空侧镜像验证
- [`p15_h1_h2_exit_report.md`](p15_h1_h2_exit_report.md) — P1.5 R3：H1/H2 出场复合验证
- [`p15_h3_ma_exit.md`](p15_h3_ma_exit.md) — P1.5 H3：结构出场（收盘跌破 EMA21）
- [`p15_h4_time_decay.md`](p15_h4_time_decay.md) — P1.5 H4：时间衰减紧缩出场
- [`p15_h5_vol_adaptive.md`](p15_h5_vol_adaptive.md) — P1.5 H5：波动率自适应障碍
- [`p15_h9_report.md`](p15_h9_report.md) — P1.5 R1'：H9 高层趋势过滤复测与推广
- [`p1_15m_arbusdt_screenshot_model_probe_20260901.md`](p1_15m_arbusdt_screenshot_model_probe_20260901.md) — ARBUSDT 截图：15m Grade-A 模型单样本检测回放（2026-09-01）
- [`p1_15m_ashare_grade_a_yolo_latest_20260902.md`](p1_15m_ashare_grade_a_yolo_latest_20260902.md) — P1：最新全 A 股 15m Grade-A YOLO 跨市场扫描（2026-09-02）
- [`p1_15m_ashare_grade_a_yolo_latest_standard_retail_20260902.md`](p1_15m_ashare_grade_a_yolo_latest_standard_retail_20260902.md) — 最新全 A 股 15m 命中：普通沪深主板账户过滤版（2026-09-02）
- [`p1_15m_grade_a_assisted_future40_20260907.md`](p1_15m_grade_a_assisted_future40_20260907.md) — YOLO 人工审核简化与未来 40 根对照
- [`p1_15m_grade_a_labelstudio_manual_20260907.md`](p1_15m_grade_a_labelstudio_manual_20260907.md) — 完整 YOLO 数据已转入 Label Studio 手工标注
- [`p1_15m_grade_a_owner_calibration_20260907.md`](p1_15m_grade_a_owner_calibration_20260907.md) — YOLO 提准第一步：盲审校准包与单变量训练准备
- [`p1_15m_ma_launch_boundary_review9000_20260826.md`](p1_15m_ma_launch_boundary_review9000_20260826.md) — 15m 六均线启动 9000 候选逐样本类别与边界审核入口
- [`p1_15m_ma_launch_dataset_release_gate9000_20260826.md`](p1_15m_ma_launch_dataset_release_gate9000_20260826.md) — 15m 六均线 9000 候选 P1 数据集 release 门
- [`p1_15m_ma_launch_five_model_alluniverse_20260831.md`](p1_15m_ma_launch_five_model_alluniverse_20260831.md) — 五个 15m 均线密集检测模型：近三天全币种冻结对照（2026-09-01）
- [`p1_15m_ma_launch_grade_a_daily_movers_202510_20260903.md`](p1_15m_ma_launch_grade_a_daily_movers_202510_20260903.md) — 2025-10 每日涨跌幅 Top5+Top5：Grade-A 15m 数据集候选挖掘（2026-09-03）
- [`p1_15m_ma_launch_grade_a_daily_movers_5000_20260903.md`](p1_15m_ma_launch_grade_a_daily_movers_5000_20260903.md) — 每日涨跌幅 Top5+Top5：Grade-A 15m 5,000+ 候选挖掘（2026-09-03）
- [`p1_15m_ma_launch_ma_box_review50_20260827.md`](p1_15m_ma_launch_ma_box_review50_20260827.md) — 15m 六均线密集框协议 Review50：固定 W20、模型像素下限与负样本冲突审计
- [`p1_15m_ma_launch_negative_parity_hardval_20260827.md`](p1_15m_ma_launch_negative_parity_hardval_20260827.md) — P1：15m 负样本两项问题审计与 hard-val 补集（2026-08-27）
- [`p1_15m_ma_launch_owner_grade_a8000_20260828.md`](p1_15m_ma_launch_owner_grade_a8000_20260828.md) — P1：15m 均线密集 A 级正样本扩容到 8,000 张（2026-08-28）
- [`p1_15m_ma_launch_owner_grade_a8000_960_epoch6_diagnosis_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_960_epoch6_diagnosis_20260829.md) — 960 模型为何第 6 轮成为最佳（2026-08-29）
- [`p1_15m_ma_launch_owner_grade_a8000_eth30d_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_eth30d_20260829.md) — ETHUSDT.P 近 30 日 Grade-A epoch-6 模型扫描（2026-08-29）
- [`p1_15m_ma_launch_owner_grade_a8000_hot3d_1280_20260830.md`](p1_15m_ma_launch_owner_grade_a8000_hot3d_1280_20260830.md) — 同一热门币快照：1280 全量训练模型与 960 模型的冻结对照（2026-08-30）
- [`p1_15m_ma_launch_owner_grade_a8000_hot3d_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_hot3d_20260829.md) — 近 3 个完整日热门币 Grade-A 模型扫描（2026-08-29）
- [`p1_15m_ma_launch_owner_grade_a8000_neg24000_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_neg24000_20260829.md) — 15m A级正样本的匹配负样本数据质量报告
- [`p1_15m_ma_launch_owner_grade_a8000_neg24000_hl2_train1280_20260903.md`](p1_15m_ma_launch_owner_grade_a8000_neg24000_hl2_train1280_20260903.md) — 15m Grade-A 六均线 close→HL2 单变量复训（2026-09-03/04）
- [`p1_15m_ma_launch_owner_grade_a8000_neg24000_train960_20260829.md`](p1_15m_ma_launch_owner_grade_a8000_neg24000_train960_20260829.md) — 15m Grade-A 8,000 正例 + 24,000 匹配负例 YOLO11s 960 训练报告
- [`p1_15m_ma_launch_owner_yolo_20260827_fullcontext_analysis_20260828.md`](p1_15m_ma_launch_owner_yolo_20260827_fullcontext_analysis_20260828.md) — 2026-08-27 全部信号：高清全景复核与详细数据分析
- [`p1_15m_ma_launch_owner_yolo_causal_semantic_gate_20260902.md`](p1_15m_ma_launch_owner_yolo_causal_semantic_gate_20260902.md) — P1：YOLO 提案 + 因果语义门配对验证（2026-09-02）
- [`p1_15m_ma_launch_owner_yolo_dataset10000_20260827.md`](p1_15m_ma_launch_owner_yolo_dataset10000_20260827.md) — P1 · 15m 均线密集启动 10,000 正例 + 10,000 负例 YOLO 数据集
- [`p1_15m_ma_launch_owner_yolo_eth30d_20260828.md`](p1_15m_ma_launch_owner_yolo_eth30d_20260828.md) — ETHUSDT.P 近 30 日原 Owner-YOLO 扫描（2026-08-28）
- [`p1_15m_ma_launch_owner_yolo_eth30d_confidence_20260828.md`](p1_15m_ma_launch_owner_yolo_eth30d_confidence_20260828.md) — ETHUSDT.P 近 30 日模型置信度分析（2026-08-28）
- [`p1_15m_ma_launch_owner_yolo_neg30000_20260827.md`](p1_15m_ma_launch_owner_yolo_neg30000_20260827.md) — P1 · 15m 均线密集启动 10,000 正例 + 30,000 负例 YOLO 数据集
- [`p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.md`](p1_15m_ma_launch_owner_yolo_neg30000_train960_20260828.md) — 15m Owner 弱标签 10,000 正 + 30,000 负 YOLO 960 训练报告
- [`p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.md`](p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.md) — 15m Owner-YOLO 检测框与实际训练图语义对照（2026-08-28）
- [`p1_15m_ma_launch_owner_yolo_recent5d_20260828.md`](p1_15m_ma_launch_owner_yolo_recent5d_20260828.md) — 新 Owner YOLO：最近五个完整 UTC 日 Top20 扫描报告
- [`p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.md`](p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.md) — 最近五日 Top20：原始 YOLO 框与单 Episode 复核修正版
- [`p1_15m_ma_launch_review_parity_v2_20260826.md`](p1_15m_ma_launch_review_parity_v2_20260826.md) — 15m t-3 因果训练图 / 完成走势审核图一致性修复 v2
- [`p1_15m_ma_launch_t3_actual_model_input_audit_20260827.md`](p1_15m_ma_launch_t3_actual_model_input_audit_20260827.md) — 15m t-3 YOLO：模型实际训练输入审计
- [`p1_15m_ma_launch_t3_daily_movers3d_20260826.md`](p1_15m_ma_launch_t3_daily_movers3d_20260826.md) — 最近三天每日绝对涨跌幅 Top20：15m t-3 模型扫描报告
- [`p1_15m_ma_launch_t3_label_semantics_audit_20260827.md`](p1_15m_ma_launch_t3_label_semantics_audit_20260827.md) — 15m t-3 YOLO 信号框语义审计：K 线框与均线密集框错位
- [`p1_15m_ma_launch_t3_yolo10000_20260826.md`](p1_15m_ma_launch_t3_yolo10000_20260826.md) — 15m 六均线密集启动 t-3 弱标签数据集与 YOLO 训练报告
- [`p1_15m_ma_launch_t3_yolo10000_imgsz1280_20260827.md`](p1_15m_ma_launch_t3_yolo10000_imgsz1280_20260827.md) — 15m 六均线密集启动 t-3：原图宽度 imgsz=1280 重训报告
- [`p1_15m_ma_launch_transition_box_review50_20260827.md`](p1_15m_ma_launch_transition_box_review50_20260827.md) — P1：15m 启动源区两段式框 Review50（2026-08-27）
- [`p1_15m_six_ma_smoothness_visibility_audit_20260902.md`](p1_15m_six_ma_smoothness_visibility_audit_20260902.md) — 15m 六均线平滑度与像素可见度审计（2026-09-02）
- [`p1_15m_yolo_color_semantics_audit_20260903.md`](p1_15m_yolo_color_semantics_audit_20260903.md) — 15m YOLO K 线与六均线颜色语义审计（2026-09-03）
- [`p1_1h_filusdt_grade_a_recent5d_probe_20260903.md`](p1_1h_filusdt_grade_a_recent5d_probe_20260903.md) — FILUSDT.P 1h 最近5天：冻结 Grade-A 模型逐小时回放（2026-09-03/04）
- [`p1_1h_filusdt_model_first_breakout_gate_20260904.md`](p1_1h_filusdt_model_first_breakout_gate_20260904.md) — FILUSDT.P 1h：模型先检测、代码再确认站上线（2026-09-04）
- [`p1_1h_filusdt_model_first_standing_gate_20260904.md`](p1_1h_filusdt_model_first_standing_gate_20260904.md) — P1 — FILUSDT.P 1h 模型先、当前站位代码后诊断（2026-09-04）
- [`p1_1h_okx_model_first_standing_top10_20260904.md`](p1_1h_okx_model_first_standing_top10_20260904.md) — P1 — OKX 全市场 1h：模型先检测、当前站位代码后 Top-10（2026-09-04）
- [`p1_4h_ma_launch_yolo_alluniverse_20260901.md`](p1_4h_ma_launch_yolo_alluniverse_20260901.md) — 4h 最新行情 YOLO 全币种扫描（2026-09-01）
- [`p1_4h_ma_launch_yolo_halfmonth_20260901.md`](p1_4h_ma_launch_yolo_halfmonth_20260901.md) — 4h YOLO 全币种最近半个月检测实验（2026-09-01）
- [`p1_4h_ma_launch_yolo_halfmonth_owner_rejection_20260902.md`](p1_4h_ma_launch_yolo_halfmonth_owner_rejection_20260902.md) — P1：4h YOLO 半月语义门 Owner 终审否决（2026-09-02）
- [`p1_4h_ma_launch_yolo_halfmonth_semantic_gate_20260902.md`](p1_4h_ma_launch_yolo_halfmonth_semantic_gate_20260902.md) — P1：4h YOLO 最近半个月因果语义门复扫（2026-09-02）
- [`p1_4h_ma_launch_yolo_latest_20260901.md`](p1_4h_ma_launch_yolo_latest_20260901.md) — 4h 最新行情 YOLO 扫描（2026-09-01）
- [`p1_altcoin_1d_k1k2_early_launch_holdout_20260905.md`](p1_altcoin_1d_k1k2_early_launch_holdout_20260905.md) — 山寨币日线 K1→K2：早启动 V4 最终 Holdout
- [`p1_altcoin_1d_k1k2_episode_runner_20260905.md`](p1_altcoin_1d_k1k2_episode_runner_20260905.md) — 山寨币日线 K1→K2：稀疏趋势 Episode 与动态退出审计
- [`p1_altcoin_1d_k1k2_market_context_20260905.md`](p1_altcoin_1d_k1k2_market_context_20260905.md) — 山寨币日线 K1→K2：市场广度共振 V3 与趋势接管诊断
- [`p1_altseason_donchian_ewmac_20260910.md`](p1_altseason_donchian_ewmac_20260910.md) — 山寨强势行情：Donchian 与 EWMAC 固定规则回测
- [`p1_altseason_multivenue_20260910.md`](p1_altseason_multivenue_20260910.md) — spike · 跨交易所山寨趋势研究：从抓到启动，到留下利润
- [`p1_ashare_grade_a_yolo_1h4h_long_sina_20260902.md`](p1_ashare_grade_a_yolo_1h4h_long_sina_20260902.md) — A 股普通主板 1h / 会话 4h 多头扫描（2026-09-02）
- [`p1_ashare_grade_a_yolo_1h4h_long_source_preflight_failure_20260902.md`](p1_ashare_grade_a_yolo_1h4h_long_source_preflight_failure_20260902.md) — A 股 1h / 会话 4h 多头扫描：数据源预检失败（2026-09-02）
- [`p1_b2_short_l2_backtest_20260811.md`](p1_b2_short_l2_backtest_20260811.md) — Local Signal V2 B2：候选密度与收益诊断
- [`p1_bico_194r_exit_case_20260910.md`](p1_bico_194r_exit_case_20260910.md) — BICO 194R：最高浮盈与可执行退出
- [`p1_btc_bb_stoch_holdout_acceptance_20260917.md`](p1_btc_bb_stoch_holdout_acceptance_20260917.md) — BTC 5m · BB × Stoch 最终验收（holdout）
- [`p1_btc_bb_stoch_optimization_20260916.md`](p1_btc_bb_stoch_optimization_20260916.md) — BTC 5m · BB × Stoch 315 组参数搜索
- [`p1_btc_xau_bb_stoch_timeframes_20260916.md`](p1_btc_xau_bb_stoch_timeframes_20260916.md) — BB × Stoch 换市场换周期：BTC 5m/1m、XAU 1m
- [`p1_btcusdtp_15m_multifactor_confluence_20260904.md`](p1_btcusdtp_15m_multifactor_confluence_20260904.md) — P1：BTCUSDT.P 15m 多因子共振与特征工程审计（2026-09-04）
- [`p1_btcusdtp_15m_runner_isolation_20260904.md`](p1_btcusdtp_15m_runner_isolation_20260904.md) — P1：BTCUSDT.P 15m 趋势接管交易可识别性审计（2026-09-04）
- [`p1_btcusdtp_15m_trend_refactor_20260904.md`](p1_btcusdtp_15m_trend_refactor_20260904.md) — P1：BTCUSDT.P 15m K1→K2 高召回与均线趋势退出重构（2026-09-04）
- [`p1_btcusdtp_15m_trend_regime_live_entry_20260904.md`](p1_btcusdtp_15m_trend_regime_live_entry_20260904.md) — P1：BTCUSDT.P 15m 趋势状态去重与实时活性门审计（2026-09-04）
- [`p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md`](p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md) — P1：BTCUSDT.P 1h K1→K2 Owner Causal V2 回测（2026-09-04）
- [`p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904_erratum.md`](p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904_erratum.md) — 勘误：BTCUSDT.P 1h Owner Causal V2 的 holdout 读取声明
- [`p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md`](p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md) — P1：BTCUSDT.P 1h Pine v8 近半年逐笔回测（2026-09-04）
- [`p1_btcusdtp_genuine_flow_coverage_v34_20260907.md`](p1_btcusdtp_genuine_flow_coverage_v34_20260907.md) — Genuine Flow Coverage · V34
- [`p1_btcusdtp_hourly_background_support_v23_20260907.md`](p1_btcusdtp_hourly_background_support_v23_20260907.md) — Entry Comparison Coverage
- [`p1_btcusdtp_hourly_breadth_change_v22_20260907.md`](p1_btcusdtp_hourly_breadth_change_v22_20260907.md) — External Rank Change
- [`p1_btcusdtp_hourly_breadth_v21_20260906.md`](p1_btcusdtp_hourly_breadth_v21_20260906.md) — External Rank Pressure
- [`p1_btcusdtp_hourly_cadence_v9_20260906.md`](p1_btcusdtp_hourly_cadence_v9_20260906.md) — Slower Checks, Still Losing
- [`p1_btcusdtp_hourly_classifier_economics_v30_20260907.md`](p1_btcusdtp_hourly_classifier_economics_v30_20260907.md) — Classifier Economics · V30
- [`p1_btcusdtp_hourly_classifier_support_v29_20260907.md`](p1_btcusdtp_hourly_classifier_support_v29_20260907.md) — Trend Classifier Support
- [`p1_btcusdtp_hourly_dual_partial_v16_20260906.md`](p1_btcusdtp_hourly_dual_partial_v16_20260906.md) — BTC Hourly: Partial Profit Realization
- [`p1_btcusdtp_hourly_failed_confirm_v18_20260906.md`](p1_btcusdtp_hourly_failed_confirm_v18_20260906.md) — Hourly Trend Exit Confirmation
- [`p1_btcusdtp_hourly_failed_launch_v17_20260906.md`](p1_btcusdtp_hourly_failed_launch_v17_20260906.md) — BTC Hourly: Fast Reversal Exits
- [`p1_btcusdtp_hourly_failed_reduce_v19_20260906.md`](p1_btcusdtp_hourly_failed_reduce_v19_20260906.md) — Confirmed Risk Reduction
- [`p1_btcusdtp_hourly_fixed_clock_v24_20260907.md`](p1_btcusdtp_hourly_fixed_clock_v24_20260907.md) — Entry Persistence Audit
- [`p1_btcusdtp_hourly_frozen_ma_v12_20260906.md`](p1_btcusdtp_hourly_frozen_ma_v12_20260906.md) — BTC Frozen MA Exit V12
- [`p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md`](p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md) — 1h 启动与跨周期退出：七轮盈利验证
- [`p1_btcusdtp_hourly_launch_v11_20260906.md`](p1_btcusdtp_hourly_launch_v11_20260906.md) — BTC Launch Deadline V11
- [`p1_btcusdtp_hourly_management_v8_20260906.md`](p1_btcusdtp_hourly_management_v8_20260906.md) — Slower Exits, Still No Edge
- [`p1_btcusdtp_hourly_native_exit_v15_20260906.md`](p1_btcusdtp_hourly_native_exit_v15_20260906.md) — Native Exit Timing — BTC Hourly V15
- [`p1_btcusdtp_hourly_prior_breakout_v14_20260906.md`](p1_btcusdtp_hourly_prior_breakout_v14_20260906.md) — BTC Hourly Breakout Support
- [`p1_btcusdtp_hourly_prior_colour_v13_20260906.md`](p1_btcusdtp_hourly_prior_colour_v13_20260906.md) — BTC Prior4h Colour V13
- [`p1_btcusdtp_hourly_structure_event_support_v25_20260907.md`](p1_btcusdtp_hourly_structure_event_support_v25_20260907.md) — Structure Event Support
- [`p1_btcusdtp_hourly_structure_v20_20260906.md`](p1_btcusdtp_hourly_structure_v20_20260906.md) — Hourly Structure Gate
- [`p1_btcusdtp_hourly_support_v10_20260906.md`](p1_btcusdtp_hourly_support_v10_20260906.md) — BTC Hourly Support Audit
- [`p1_btcusdtp_hourly_volume_wave_economics_v32_20260907.md`](p1_btcusdtp_hourly_volume_wave_economics_v32_20260907.md) — Volume Wave Economics · V32
- [`p1_btcusdtp_hourly_volume_wave_support_v31_20260907.md`](p1_btcusdtp_hourly_volume_wave_support_v31_20260907.md) — Pre-K1 Volume Support · V31
- [`p1_btcusdtp_hourly_vwma_background_v27_20260907.md`](p1_btcusdtp_hourly_vwma_background_v27_20260907.md) — VWMA Background Support
- [`p1_btcusdtp_hourly_vwma_fixed_clock_v28_20260907.md`](p1_btcusdtp_hourly_vwma_fixed_clock_v28_20260907.md) — VWMA Entry Persistence
- [`p1_btcusdtp_hourly_vwma_reference_support_v26_20260907.md`](p1_btcusdtp_hourly_vwma_reference_support_v26_20260907.md) — SMA vs VWMA Entry Support
- [`p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md`](p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md) — BTCUSDT.P 15min / 5min 独立 K1→K2 研究（2026-09-04）
- [`p1_btcusdtp_k1k2_15m_5m_parameter_optimization_preholdout_20260904.md`](p1_btcusdtp_k1k2_15m_5m_parameter_optimization_preholdout_20260904.md) — BTCUSDT.P 15m / 5m K1→K2 独立参数优化（pre-holdout）
- [`p1_btcusdtp_k1k2_15m_dynamic_stop_preholdout_20260904.md`](p1_btcusdtp_k1k2_15m_dynamic_stop_preholdout_20260904.md) — BTCUSDT.P 15m K1→K2 动态 / 自动止损实验（2026-09-04）
- [`p1_btcusdtp_k1k2_15m_gap_min_confirmation_20260904.md`](p1_btcusdtp_k1k2_15m_gap_min_confirmation_20260904.md) — BTCUSDT.P 15min K1→K2 最小距离确认（2026-09-04）
- [`p1_btcusdtp_k1k2_15m_two_stage_k2_freqtrade_preholdout_20260904.md`](p1_btcusdtp_k1k2_15m_two_stage_k2_freqtrade_preholdout_20260904.md) — BTCUSDT.P 15m K1→K2 两阶段确认 + Freqtrade 全量验证
- [`p1_btcusdtp_k1k2_breakout_entry_preholdout_20260904.md`](p1_btcusdtp_k1k2_breakout_entry_preholdout_20260904.md) — BTCUSDT.P K1→K2：方向突破确认入场（15m / 5m）
- [`p1_btcusdtp_k1k2_causal_failure_map_20260904.md`](p1_btcusdtp_k1k2_causal_failure_map_20260904.md) — BTCUSDT.P 15min / 5min K1→K2 因果失败地图（2026-09-04）
- [`p1_btcusdtp_k1k2_fixed_target_preholdout_20260904.md`](p1_btcusdtp_k1k2_fixed_target_preholdout_20260904.md) — BTCUSDT.P K1→K2：固定止盈倍数（15m / 5m）
- [`p1_btcusdtp_k1k2_genuine_flow_alignment_v35_20260907.md`](p1_btcusdtp_k1k2_genuine_flow_alignment_v35_20260907.md) — K1/K2 Flow Clocks · V35
- [`p1_btcusdtp_k1k2_partial_runner_preholdout_20260904.md`](p1_btcusdtp_k1k2_partial_runner_preholdout_20260904.md) — BTCUSDT.P K1→K2：3R 部分止盈 + 8R Runner（15m / 5m）
- [`p1_btcusdtp_k1k2_protection_trigger_preholdout_20260904.md`](p1_btcusdtp_k1k2_protection_trigger_preholdout_20260904.md) — BTCUSDT.P K1→K2：盈利保护触发点（15m / 5m）
- [`p1_btcusdtp_k1k2_stop_buffer_preholdout_20260904.md`](p1_btcusdtp_k1k2_stop_buffer_preholdout_20260904.md) — BTCUSDT.P K1→K2：K2 极值外 ATR 止损缓冲实验（15m / 5m）
- [`p1_btcusdtp_k1k2_sweep_reclaim_entry_preholdout_20260904.md`](p1_btcusdtp_k1k2_sweep_reclaim_entry_preholdout_20260904.md) — BTCUSDT.P K1→K2：扫过 K2 极值后收复入场（15m / 5m）
- [`p1_btcusdtp_ma_smoothness_visual_comparison_20260903.md`](p1_btcusdtp_ma_smoothness_visual_comparison_20260903.md) — BTCUSDT.P 六均线平滑度视觉对照（2026-09-03）
- [`p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.md`](p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.md) — K1/K2 Delayed Entry · V39
- [`p1_btcusdtp_owner_k1k2_genuine_flow_v36_20260907.md`](p1_btcusdtp_owner_k1k2_genuine_flow_v36_20260907.md) — K1/K2 Genuine Flow Test · V36
- [`p1_btcusdtp_owner_k1k2_pending_entry_v38_20260907.md`](p1_btcusdtp_owner_k1k2_pending_entry_v38_20260907.md) — K1/K2 Pending Entry Audit · V38
- [`p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.md`](p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.md) — K1/K2 Exit Timing Test · V37
- [`p1_chartart_bbrsi_martingale_20260915.md`](p1_chartart_bbrsi_martingale_20260915.md) — ChartArt BB＋RSI v1.1：原逻辑与亏损后翻倍
- [`p1_chartart_bbrsi_v12_longonly_btceth_15m_20260917.md`](p1_chartart_bbrsi_v12_longonly_btceth_15m_20260917.md) — ChartArt BB+RSI v1.2 只做多：BTC / ETH USDT 永续 15m 预 holdout 回测
- [`p1_chartprime_public_confluence_audit_20260906.md`](p1_chartprime_public_confluence_audit_20260906.md) — ChartPrime Confluence Audit
- [`p1_crypto_grade_a_yolo_mtf_latest_20260903.md`](p1_crypto_grade_a_yolo_mtf_latest_20260903.md) — P1：最新加密行情四周期 Grade-A YOLO 排序图审（2026-09-03）
- [`p1_eth_bb_stoch_backtest_20260916.md`](p1_eth_bb_stoch_backtest_20260916.md) — ETH 5m · BB × Stoch v2 回测
- [`p1_eth_bb_stoch_indicator_20260915.md`](p1_eth_bb_stoch_indicator_20260915.md) — ETH 5m · BB × Stoch 反转指标
- [`p1_eth_bb_stoch_longrun_20260916.md`](p1_eth_bb_stoch_longrun_20260916.md) — ETH 5m · BB × Stoch 28 个月长周期诊断
- [`p1_eth_bb_stoch_optimization_20260916.md`](p1_eth_bb_stoch_optimization_20260916.md) — ETH 5min · BB × Stoch 参数搜索
- [`p1_eth_bb_stoch_rsi_filter_20260916.md`](p1_eth_bb_stoch_rsi_filter_20260916.md) — ETH 5m · BB × Stoch 加 Parabolic RSI 区域过滤
- [`p1_eth_bb_stoch_strategy_20260916.md`](p1_eth_bb_stoch_strategy_20260916.md) — ETH 5m · BB × Stoch 策略 v2
- [`p1_eth_ma120_longest_runs_20260914.md`](p1_eth_ma120_longest_runs_20260914.md) — ETHUSDT.P：SMA120 / EMA120 同侧最长连续区间
- [`p1_eth_xau_15m_asset_specific_k1k2_20260905.md`](p1_eth_xau_15m_asset_specific_k1k2_20260905.md) — ETH / XAU 15m：品种专属 K1→K2 趋势策略审计
- [`p1_ethusdtp_15m_causal_confluence_20260905.md`](p1_ethusdtp_15m_causal_confluence_20260905.md) — ETHUSDT.P 15m：因果共振筛选与扩张门外推失败审计（V17/V18）
- [`p1_ethusdtp_15m_gradual_take_profit_20260905.md`](p1_ethusdtp_15m_gradual_take_profit_20260905.md) — P1：ETHUSDT.P 15m 趋势单渐进止盈 V16（2026-09-05）
- [`p1_fixed_w10_blind_audit_pack_20260820.md`](p1_fixed_w10_blind_audit_pack_20260820.md) — P1 fixed-W10 门禁修复、artifact 谱系与盲审包（2026-08-20）
- [`p1_fixed_w10_canonical_ohlc_triage_v2_20260821.md`](p1_fixed_w10_canonical_ohlc_triage_v2_20260821.md) — P1 统一原始 OHLC 全量筛选包 v2（2026-08-21）
- [`p1_fixed_w10_original_source_triage_20260821.md`](p1_fixed_w10_original_source_triage_20260821.md) — P1 fixed-W10 原始来源图全量筛选包（2026-08-21）
- [`p1_goal_martingale_path_20260915.md`](p1_goal_martingale_path_20260915.md) — 倍投可行路径搜索 · 第 1 轮：前提检验
- [`p1_goal_martingale_path_r2_20260915.md`](p1_goal_martingale_path_r2_20260915.md) — 倍投可行路径搜索 · 第 2 轮：行情能否事前识别 & 亏后加码是否成立
- [`p1_goal_martingale_path_r3_20260915.md`](p1_goal_martingale_path_r3_20260915.md) — 倍投可行路径搜索 · 第 3 轮：全仓库普查 + 破产概率
- [`p1_goal_martingale_path_r4_20260915.md`](p1_goal_martingale_path_r4_20260915.md) — 倍投可行路径搜索 · 第 4 轮：零期望压力测试与亏损聚集分解
- [`p1_gold_label_quality_20260820.md`](p1_gold_label_quality_20260820.md) — P1 — 固定 W10 金标的标签错误率（2026-08-20）
- [`p1_ifvg_lrl_eth_20260915.md`](p1_ifvg_lrl_eth_20260915.md) — ETH 3分钟 iFVG＋LRL v1：未显示扣费后盈利能力
- [`p1_imacd_altcoin_trends_20260909.md`](p1_imacd_altcoin_trends_20260909.md) — Spike｜高波动山寨启动与趋势持有研究
- [`p1_imacd_formation_memory_20260908.md`](p1_imacd_formation_memory_20260908.md) — SPIKE · 均线形成记忆与价格位置：第二轮验证
- [`p1_imacd_launch_context_20260908.md`](p1_imacd_launch_context_20260908.md) — SPIKE · 启动行情降噪：形成、突破、多周期分开验证
- [`p1_imacd_startup_quality_20260908.md`](p1_imacd_startup_quality_20260908.md) — SPIKE · IMACD 启动质量实证
- [`p1_imacd_yolo_confirmation_20260908.md`](p1_imacd_yolo_confirmation_20260908.md) — IMACD → YOLO 延迟确认试验：接线可行，盈利与去噪仍待验证
- [`p1_imacd_yolo_expanded_20260908.md`](p1_imacd_yolo_expanded_20260908.md) — IMACD → YOLO 扩大检查：筛选生效，等待与形态身份仍需检验
- [`p1_imacd_yolo_followthrough_20260908.md`](p1_imacd_yolo_followthrough_20260908.md) — IMACD + YOLO 后续走势：24根统计与完整事后图
- [`p1_imacd_yolo_timeframes_20260908.md`](p1_imacd_yolo_timeframes_20260908.md) — IMACD → YOLO 的1H/4H迁移：先核对确认与等待时钟
- [`p1_launch_quality_20260910.md`](p1_launch_quality_20260910.md) — spike · 1H 启动质量：过滤假启动，会不会也过滤大赢家？
- [`p1_local_signal_v2_position_shortcut_20260811.md`](p1_local_signal_v2_position_shortcut_20260811.md) — Local Signal V2 位置 shortcut 纠错（2026-08-11）
- [`p1_local_signal_v2_prereg_20260810.md`](p1_local_signal_v2_prereg_20260810.md) — P1 局部因果窗口对照预注册
- [`p1_local_signal_v2_report_20260811.md`](p1_local_signal_v2_report_20260811.md) — Local Signal V2 P1 局部因果窗口对照报告
- [`p1_local_signal_v2_stagea_gap_to_owner_target_20260811.md`](p1_local_signal_v2_stagea_gap_to_owner_target_20260811.md) — Local Signal V2：昨晚 3060 Stage A 与 Owner 最终目标差距复盘
- [`p1_local_signal_v2_stagea_position_eval_20260811.md`](p1_local_signal_v2_stagea_position_eval_20260811.md) — Local Signal V2 Stage A 训练与分位置诊断（2026-08-11）
- [`p1_local_signal_v2_stageb_cold_report.md`](p1_local_signal_v2_stageb_cold_report.md) — P1 — Local Signal V2 Stage B 冷启动（owner_lsv2_stageb_cold）
- [`p1_ma_launch_label_leakage_and_edge_20260830.md`](p1_ma_launch_label_leakage_and_edge_20260830.md) — MA 密集启动：标签泄漏与真实 edge（2026-08-30）
- [`p1_ma_rope_prefilter_20260821.md`](p1_ma_rope_prefilter_20260821.md) — P1 · 六均线“拧成一股绳”代码预筛与数据扩充入口（2026-08-21）
- [`p1_ma_shift_stoch_eth_month_20260915.md`](p1_ma_shift_stoch_eth_month_20260915.md) — ETH 近一月：15分钟颜色 × 5分钟 Stoch
- [`p1_ma_stoch_exit_optimization_20260915.md`](p1_ma_stoch_exit_optimization_20260915.md) — ETH：退出、止盈、止损优化 v1
- [`p1_ma_stoch_exit_optimization_v2_20260915.md`](p1_ma_stoch_exit_optimization_v2_20260915.md) — ETH止盈止损第二轮：更宽止损与不同获利退出
- [`p1_mainstream_super_trend_20260910.md`](p1_mainstream_super_trend_20260910.md) — 主流币超级趋势：固定规则迁移回测
- [`p1_owner_eth_perfect_platform_semantic_audit_20260811.md`](p1_owner_eth_perfect_platform_semantic_audit_20260811.md) — ETH 完美平台语义审查：短延迟、多位置、不自动贴标签
- [`p1_owner_eth_shortdelay_boundary_contract_20260811.md`](p1_owner_eth_shortdelay_boundary_contract_20260811.md) — ETH完美平台：竖线内核心与3–5根短延迟合同
- [`p1_owner_eth_shortdelay_calibration30_20260811.md`](p1_owner_eth_shortdelay_calibration30_20260811.md) — P1 Owner ETH 短延迟动态窗口 30 张校准报告（2026-08-11）
- [`p1_owner_eth_shortdelay_codex_firstpass_20260811.md`](p1_owner_eth_shortdelay_codex_firstpass_20260811.md) — P1 Owner ETH 短延迟语义 Codex 一审（2026-08-11）
- [`p1_owner_eth_shortdelay_dynamic_review200_20260811.md`](p1_owner_eth_shortdelay_dynamic_review200_20260811.md) — P1 Owner ETH 空头动态短窗 200 张扩展、一审与逐图改框（2026-08-11）
- [`p1_owner_gold_center_crop_review_20260811.md`](p1_owner_gold_center_crop_review_20260811.md) — P1 原始空头金标中心裁切审核
- [`p1_owner_long_candidate_manifest_20260824.md`](p1_owner_long_candidate_manifest_20260824.md) — P1 Owner-long 待审核候选 manifest
- [`p1_owner_long_candidate_manifest_v2_20260824.md`](p1_owner_long_candidate_manifest_v2_20260824.md) — P1 Owner-long 待审核候选 manifest v2
- [`p1_owner_long_dataset_lineage_audit_20260821.md`](p1_owner_long_dataset_lineage_audit_20260821.md) — P1 Owner-long 做多检测器数据谱系与镜像方案审计
- [`p1_owner_manual_order_card_20260916.md`](p1_owner_manual_order_card_20260916.md) — 人工开单卡 · 均线密集启动
- [`p1_owner_manual_trading_system_20260916.md`](p1_owner_manual_trading_system_20260916.md) — 人工交易手册：均线密集、V9与YOLO怎样一起用
- [`p1_owner_okx_history_20260916.md`](p1_owner_okx_history_20260916.md) — 你的交易复盘与执行系统
- [`p1_owner_short_gold_center_dataset_20260811.md`](p1_owner_short_gold_center_dataset_20260811.md) — P1 Owner空头金标中心裁切全量数据集
- [`p1_owner_short_gold_center_recent2d_holdout_20260811.md`](p1_owner_short_gold_center_recent2d_holdout_20260811.md) — Owner-short compact YOLO 最近2天全市场回放（2026-08-11）
- [`p1_owner_short_positive_refilter_20260821.md`](p1_owner_short_positive_refilter_20260821.md) — P1 Owner 旧训练正例原图精筛包（2026-08-21）
- [`p1_pine_v12f_grade_a_yolo_backtest_20260903.md`](p1_pine_v12f_grade_a_yolo_backtest_20260903.md) — P1：ETH 15m Pine V12F × Grade-A YOLO 延迟融合回测（2026-09-03）
- [`p1_pine_v12f_grade_a_yolo_fusion_20260903.md`](p1_pine_v12f_grade_a_yolo_fusion_20260903.md) — P1：ETH 15m Pine V12F × Grade-A YOLO 区间融合审计（2026-09-03）
- [`p1_preholdout_dataset_rebuild_20260803.md`](p1_preholdout_dataset_rebuild_20260803.md) — P1-DATA：pre-holdout immutable short L2 dataset 重建验收
- [`p1_release_eth_multitf_20260914.md`](p1_release_eth_multitf_20260914.md) — P1 ETH multi-timeframe release replay — 2026-09-14
- [`p1_spike_account_growth_20260913.md`](p1_spike_account_growth_20260913.md) — SPIKE 账户冻结回放技术报告
- [`p1_spike_ashare_v1_v8_three_year_20260913.md`](p1_spike_ashare_v1_v8_three_year_20260913.md) — SPIKE V1 / V8：沪深主板近三年日线与周线
- [`p1_spike_burst_early_warning_20260910.md`](p1_spike_burst_early_warning_20260910.md) — SPIKE V3：结构早预警与动能确认分层
- [`p1_spike_burst_launch_recall_20260910.md`](p1_spike_burst_launch_recall_20260910.md) — SPIKE V2：渐进启动补漏与全池召回验证
- [`p1_spike_burst_noise_20260910.md`](p1_spike_burst_noise_20260910.md) — SPIKE：频繁信号的定位与单项降噪实测
- [`p1_spike_burst_three_year_20260910.md`](p1_spike_burst_three_year_20260910.md) — SPIKE 强劲爆发 V1：三年冻结规则回顾
- [`p1_spike_burst_validation_20260910.md`](p1_spike_burst_validation_20260910.md) — SPIKE 强劲爆发 V1：抓到了哪些行情，实际留下多少利润
- [`p1_spike_coin_be_review_20260912.md`](p1_spike_coin_be_review_20260912.md) — SPIKE 逐币退出复查：原退出 vs 1R 推保本
- [`p1_spike_eth3m_ict_sessions_20260914.md`](p1_spike_eth3m_ict_sessions_20260914.md) — ETH3m V8：只在ICT指定时段开仓
- [`p1_spike_eth3m_net_recovery_20260914.md`](p1_spike_eth3m_net_recovery_20260914.md) — ETH3m：净1R止盈、费用保本与整轮回本重置验证
- [`p1_spike_eth3m_partial_tp_20260914.md`](p1_spike_eth3m_partial_tp_20260914.md) — ETHUSDT.P 3分钟：分批止盈和收紧移动止损
- [`p1_spike_eth3m_recovery_20260914.md`](p1_spike_eth3m_recovery_20260914.md) — ETHUSDT.P 3分钟：1U起步的倍投与回本研究
- [`p1_spike_eth3m_september_trade_audit_20260914.md`](p1_spike_eth3m_september_trade_audit_20260914.md) — ETHUSDT.P 3 分钟 SPIKE V8：2026-09-01 至 09-14 逐笔核对
- [`p1_spike_eth_lowtf_cost_diagnostic_20260914.md`](p1_spike_eth_lowtf_cost_diagnostic_20260914.md) — ETH V8 低周期成本预算与 1R 保本诊断
- [`p1_spike_eth_martingale_20260914.md`](p1_spike_eth_martingale_20260914.md) — ETH V8 止损后倍投：1000 USDT 有限资金研究
- [`p1_spike_eth_v9_yolo_entry_20260915.md`](p1_spike_eth_v9_yolo_entry_20260915.md) — ETH 最近两个月：V9 开仓与同级／小级别 YOLO
- [`p1_spike_exit_policy_20260912.md`](p1_spike_exit_policy_20260912.md) — SPIKE V1／V6／V7：退出规则与账户风险比较
- [`p1_spike_fanshen_exit_multitf_20260914.md`](p1_spike_fanshen_exit_multitf_20260914.md) — V8 × 翻身 Stoch：六周期退出回测
- [`p1_spike_fanshen_source_audit_20260914.md`](p1_spike_fanshen_source_audit_20260914.md) — 翻身 V1 源码核验：找到旧 Stoch，尚未找到目标完整版本
- [`p1_spike_market_breadth_20260913.md`](p1_spike_market_breadth_20260913.md) — SPIKE 市场广度：冻结候选的匹配随机对照整合报告
- [`p1_spike_noise_reduction_execution_20260913.md`](p1_spike_noise_reduction_execution_20260913.md) — SPIKE V7／V8 五条降噪路线：执行与验收报告
- [`p1_spike_pepe_owner_notes_20260910.md`](p1_spike_pepe_owner_notes_20260910.md) — PEPE 4H：Owner 批注与 V4 确认含义核对
- [`p1_spike_v10_4_1h_detail_20260918.md`](p1_spike_v10_4_1h_detail_20260918.md) — SPIKE V10.4 · 1h「突破+spike」只做多——逐笔拆解：挑点比随机好，但盈亏基本跟着大盘走
- [`p1_spike_v10_4_1h_increment_20260918.md`](p1_spike_v10_4_1h_increment_20260918.md) — SPIKE V10.4 · 1h 复现与趋势线增量验证：原结果逐笔复现；联合门的「改善」主要是少做，不是做得更好
- [`p1_spike_v10_4_joint_multitf_20260918.md`](p1_spike_v10_4_joint_multitf_20260918.md) — SPIKE V10.4「突破+spike」只做多 · 6 个周期回测——没有一个周期通过；1h 最接近
- [`p1_spike_v10_long_break7_20260918.md`](p1_spike_v10_long_break7_20260918.md) — SPIKE V10（重做）：只做多 + 突破后 ≤7 根——每笔变好是因为笔数少了，后一段更差
- [`p1_spike_v10_trendline_gate_20260918.md`](p1_spike_v10_trendline_gate_20260918.md) — SPIKE V10 趋势线突破门：每笔质量明显变好，总量砍掉一半以上，后一段依然为负
- [`p1_spike_v112_1h_diagnostics_20260919.md`](p1_spike_v112_1h_diagnostics_20260919.md) — SPIKE V11.2 1h：完整回测数据与失败路径分析
- [`p1_spike_v112_audit_20260919.md`](p1_spike_v112_audit_20260919.md) — SPIKE V11.2「框内突破+spike」审查：已有负面答案，剩余问题是交易口径、一致性与前向证据
- [`p1_spike_v112_entry_extension_20260920.md`](p1_spike_v112_entry_extension_20260920.md) — V11.2：限制入场前价格推进，三个周期均未通过研究筛查
- [`p1_spike_v112_execution_20260919.md`](p1_spike_v112_execution_20260919.md) — SPIKE V11.2 三项执行逻辑研究：能救回个别亏单，尚未得到正收益改法
- [`p1_spike_v112_failure_diagnosis_20260919.md`](p1_spike_v112_failure_diagnosis_20260919.md) — SPIKE V11.2 失败交易诊断：先查入场有效性，再做局部参数检验
- [`p1_spike_v112_manage_20260919.md`](p1_spike_v112_manage_20260919.md) — V9 多头用「突破」管理持仓：加仓变差；没突破就走只在下跌段有用
- [`p1_spike_v112_one_line_audit_20260920.md`](p1_spike_v112_one_line_audit_20260920.md) — V11.2：ONE 15m白色下降线卡在第三个独立高点
- [`p1_spike_v112_sample_direction_20260919.md`](p1_spike_v112_sample_direction_20260919.md) — SPIKE 抽样 50 笔：方向、入场位置与利润兑现
- [`p1_spike_v112_selected_counts_20260919.md`](p1_spike_v112_selected_counts_20260919.md) — V11.2：Owner选定29币的15m、1h、4h交易数量
- [`p1_spike_v112_selected_returns_20260919.md`](p1_spike_v112_selected_returns_20260919.md) — V11.2选定29币收益：15m受仓位口径影响，4h盈利集中于前段
- [`p1_spike_v112_support_20260919.md`](p1_spike_v112_support_20260919.md) — SPIKE V11.2 六均线支撑单变量完整回放
- [`p1_spike_v112_trade_review_20260920.md`](p1_spike_v112_trade_review_20260920.md) — V11.2：29币570笔逐笔复盘与8张行情图
- [`p1_spike_v112_tv_parity_20260919.md`](p1_spike_v112_tv_parity_20260919.md) — SPIKE V11.2：看板止损与 TradingView 缺信号核查
- [`p1_spike_v11_box_joint_20260918.md`](p1_spike_v11_box_joint_20260918.md) — SPIKE V11.1「多头框内突破就算」单独回测：不加限制后更差，1h 明显差于 V10.4
- [`p1_spike_v11_box_trade_book_20260919.md`](p1_spike_v11_box_trade_book_20260919.md) — 突破+spike 逐笔明细 + 抽样 50 张图（框内规则）
- [`p1_spike_v11_mtf_joint_20260918.md`](p1_spike_v11_mtf_joint_20260918.md) — SPIKE V11 回测：加入上级周期突破，15m 和 1h 都没有变好，「突破+spike（上级突破）」后段反而最弱
- [`p1_spike_v12_local_touch_20260920.md`](p1_spike_v12_local_touch_20260920.md) — SPIKE V12：主高点＋局部回踩确认
- [`p1_spike_v1_eth_stops_20260911.md`](p1_spike_v1_eth_stops_20260911.md) — ETH-USDT-SWAP：原版 SPIKE Burst V1 / V6 的止损敏感性
- [`p1_spike_v1_plus_backtest_20260912.md`](p1_spike_v1_plus_backtest_20260912.md) — SPIKE V1+ 完整默认配置：两年回测
- [`p1_spike_v1_replay_ledger_link_20260911.md`](p1_spike_v1_replay_ledger_link_20260911.md) — SPIKE V1 回放记录与 v2 覆盖账本逐笔关联
- [`p1_spike_v1_triple_exit_20260914.md`](p1_spike_v1_triple_exit_20260914.md) — SPIKE V1 三规则组合退出：逐根回放
- [`p1_spike_v1_twoyear_20260911.md`](p1_spike_v1_twoyear_20260911.md) — SPIKE 强劲爆发 V1：两年三所全市场回测 — 覆盖受限账本的可执行口径修正
- [`p1_spike_v1_v8_asset_ranking_20260914.md`](p1_spike_v1_v8_asset_ranking_20260914.md) — V1与V8：币种收益榜、赢家特征与差币排除检验
- [`p1_spike_v1_v8_be05_20260914.md`](p1_spike_v1_v8_be05_20260914.md) — V1 / V8：浮盈触及 0.5R 后推开仓价，效果如何
- [`p1_spike_v1_v8_tier_lock_20260914.md`](p1_spike_v1_v8_tier_lock_20260914.md) — V1 / V8：0.5R保本＋1.5R锁0.5R，三组对照
- [`p1_spike_v1_v9_asset_trim_20260917.md`](p1_spike_v1_v9_asset_trim_20260917.md) — SPIKE V1 / V9：剔除盈利最高与最低的币种后还剩什么
- [`p1_spike_v3_confirmation_gate_20260910.md`](p1_spike_v3_confirmation_gate_20260910.md) — SPIKE V3：确认之后再抑制重复预警
- [`p1_spike_v3_density_rearm_20260910.md`](p1_spike_v3_density_rearm_20260910.md) — SPIKE G：提示减少了，但丢掉了太多及时启动
- [`p1_spike_v3_focus_20260910.md`](p1_spike_v3_focus_20260910.md) — SPIKE：减少提示之后，真正的启动还留住了吗
- [`p1_spike_v3_formation_gate_20260910.md`](p1_spike_v3_formation_gate_20260910.md) — SPIKE V3：突破前均线收拢，能否减少假启动
- [`p1_spike_v3_formation_gate_20260910_r2.md`](p1_spike_v3_formation_gate_20260910_r2.md) — SPIKE V3：突破前均线收拢，能否减少假启动
- [`p1_spike_v3_gate_diagnostic_20260910.md`](p1_spike_v3_gate_diagnostic_20260910.md) — V3 为什么信号太多，以及如何降噪
- [`p1_spike_v3_htf_gate_20260910.md`](p1_spike_v3_htf_gate_20260910.md) — SPIKE V3：只用已收盘4H背景，能否减少假启动
- [`p1_spike_v3_layered_display_20260910.md`](p1_spike_v3_layered_display_20260910.md) — V3 分层观察：保留事件，减少反复开仓的视觉暗示
- [`p1_spike_v3_noise_overview_20260910.md`](p1_spike_v3_noise_overview_20260910.md) — SPIKE V3 降噪总览：原版与六次固定探索
- [`p1_spike_v3_price_acceptance_20260910.md`](p1_spike_v3_price_acceptance_20260910.md) — SPIKE V3：突破后等待一根，价格接受研究
- [`p1_spike_v3_retest_diagnostic_20260910.md`](p1_spike_v3_retest_diagnostic_20260910.md) — SPIKE：下一根回到区间内，究竟删掉了什么
- [`p1_spike_v4_confirmed_only_20260910.md`](p1_spike_v4_confirmed_only_20260910.md) — V4：图表只显示确认信号
- [`p1_spike_v4_quiet_display_20260910.md`](p1_spike_v4_quiet_display_20260910.md) — V4 简化：默认只看预警和所属确认
- [`p1_spike_v4_saved_20260910.md`](p1_spike_v4_saved_20260910.md) — SPIKE V4 已保存到 TradingView
- [`p1_spike_v5_bidirectional_20260910.md`](p1_spike_v5_bidirectional_20260910.md) — V5 多空确认、白色信号 K 线与无边框盈亏区
- [`p1_spike_v5_display_history_20260911.md`](p1_spike_v5_display_history_20260911.md) — V5 显示与历史盈亏框修复
- [`p1_spike_v5_pepe_1h_repair_20260910.md`](p1_spike_v5_pepe_1h_repair_20260910.md) — PEPE 1H：V5 漏报修复与实际确认时间
- [`p1_spike_v5_structure_confirmation_20260910.md`](p1_spike_v5_structure_confirmation_20260910.md) — SPIKE V5：将“同一段结构完成”作为最终确认
- [`p1_spike_v6_bb_squeeze_20260912.md`](p1_spike_v6_bb_squeeze_20260912.md) — V6 × BB200 压缩：四周期固定组合回测
- [`p1_spike_v6_eth_freqtrade_20260911.md`](p1_spike_v6_eth_freqtrade_20260911.md) — SPIKE V6 ETH Freqtrade bridge：冻结镜像执行检查
- [`p1_spike_v6_volume_price_20260911.md`](p1_spike_v6_volume_price_20260911.md) — SPIKE V6：量价结构证据的 PEPE 1H 功能核对
- [`p1_spike_v6_wvf_20260912.md`](p1_spike_v6_wvf_20260912.md) — SPIKE V6 × Williams Vix Fix：组合回测与完整走势
- [`p1_spike_v7_first_launch_20260912.md`](p1_spike_v7_first_launch_20260912.md) — V7 同一段压缩的首次启动：固定对照研究
- [`p1_spike_v7_v1_compare_20260912.md`](p1_spike_v7_v1_compare_20260912.md) — SPIKE V7 BB 背景准入与归档 V1：结果交付
- [`p1_spike_v8_entry_evidence_20260914.md`](p1_spike_v8_entry_evidence_20260914.md) — V8：BB压缩段排列、六线密集与高周期入场反证
- [`p1_spike_v8_entry_process_early_exit_20260913.md`](p1_spike_v8_entry_process_early_exit_20260913.md) — V8：证据是否过时、失败突破反转与提前退出
- [`p1_spike_v8_eth3m_be_20260913.md`](p1_spike_v8_eth3m_be_20260913.md) — V8 ETH 3分钟：浮盈触及1R后推入场价保本
- [`p1_spike_v8_eth_baseline_stats_20260914.md`](p1_spike_v8_eth_baseline_stats_20260914.md) — V8 ETH 永续 3分钟 / 5分钟：原始交易统计
- [`p1_spike_v8_ict_be2_adoption_20260915.md`](p1_spike_v8_ict_be2_adoption_20260915.md) — ETH 15分钟＋ICT：加入净2R后的含费保本
- [`p1_spike_v8_ict_exits_20260915.md`](p1_spike_v8_ict_exits_20260915.md) — ETH15min＋ICT：止盈与利润保护的8套对照
- [`p1_spike_v8_ict_multitf_20260915.md`](p1_spike_v8_ict_multitf_20260915.md) — 原V8 × ICT时段：六周期对照
- [`p1_spike_v8_ict_winner_peaks_20260915.md`](p1_spike_v8_ict_winner_peaks_20260915.md) — ETH15min＋ICT：盈利单峰值与止盈诊断
- [`p1_spike_v8_lowtf_20260913.md`](p1_spike_v8_lowtf_20260913.md) — SPIKE V8 低周期：BTC、ETH 与因果日榜单回放
- [`p1_spike_v8_ma_cycle_20260913.md`](p1_spike_v8_ma_cycle_20260913.md) — V8：均线压缩、启动、扩散、再盘整的验证
- [`p1_spike_v8_noise_filter_20260913.md`](p1_spike_v8_noise_filter_20260913.md) — SPIKE V8：V7 全量信号降噪与冻结规则回放
- [`p1_spike_v8_six_filters_20260914.md`](p1_spike_v8_six_filters_20260914.md) — V8：六批 V1 观察的独立过滤回放（2026-09-14）
- [`p1_spike_v8_total2_1h_native_20260914.md`](p1_spike_v8_total2_1h_native_20260914.md) — V8 × TOTAL2 1H：TradingView 原生回测
- [`p1_spike_v9_asset_exclusions_20260915.md`](p1_spike_v9_asset_exclusions_20260915.md) — 按币种成绩排除：V9 三个固定条件
- [`p1_spike_v9_cost_be2_20260915.md`](p1_spike_v9_cost_be2_20260915.md) — V9 加入净2R后的0.2%保护：同入场与串行回测
- [`p1_spike_v9_eth_lowtf_20260915.md`](p1_spike_v9_eth_lowtf_20260915.md) — ETH V9 · 3m / 5m 完整历史回测
- [`p1_spike_v9_full_backtest_20260915.md`](p1_spike_v9_full_backtest_20260915.md) — SPIKE V9 全量回测：优于 V8，但后一年仍亏损
- [`p1_spike_v9_implementation_20260915.md`](p1_spike_v9_implementation_20260915.md) — SPIKE V9：标的、量比与 UTC 周日过滤已实现
- [`p1_spike_v9_later_year_attribution_20260917.md`](p1_spike_v9_later_year_attribution_20260917.md) — V9 后一年为什么会亏：毛优势塌到成本线以下，塌的地方是 30m
- [`p1_spike_v9_v1_comparison_20260915.md`](p1_spike_v9_v1_comparison_20260915.md) — SPIKE V9 与 V1：已有全量结果对读
- [`p1_trendline_v2_tbsl_20260918.md`](p1_trendline_v2_tbsl_20260918.md) — 下降趋势线突破 V2 · 止盈止损网格与多周期回测
- [`p1_useless_multiscale_launch_20260909.md`](p1_useless_multiscale_launch_20260909.md) — SPIKE · USELESS 多周期启动复盘与超级趋势原型
- [`p1_yolo_dataset_consolidation_20260908.md`](p1_yolo_dataset_consolidation_20260908.md) — 最新模型1043事件已带原训练框进入人工审核
- [`p1_yolo_historical_inventory_20260908.md`](p1_yolo_historical_inventory_20260908.md) — 旧2513题已建处置清单，继续优先审核最新1043题
- [`p1_yolo_historical_label_reuse_20260907.md`](p1_yolo_historical_label_reuse_20260907.md) — P1 · 历史人工标注库存与复用顺序（2026-09-07）
- [`p1_yolo_owner_annotation_audit_20260908.md`](p1_yolo_owner_annotation_audit_20260908.md) — 你已调整的 72 张图：怎样标平台、怎样看后续 150 根
- [`p1_yolo_owner_box_curation_20260907.md`](p1_yolo_owner_box_curation_20260907.md) — YOLO 历史框审核：已装工具接入与重点复核队列
- [`p1_yolo_owner_box_refinement_20260907.md`](p1_yolo_owner_box_refinement_20260907.md) — 历史人工框细化：2513个建议已进入Label Studio
- [`p1_yolo_review_entry_fix_20260908.md`](p1_yolo_review_entry_fix_20260908.md) — YOLO本周1043题审核入口已修正
- [`p1_yolo_review_future150_20260908.md`](p1_yolo_review_future150_20260908.md) — 本周YOLO审核已扩展到150根未来K线
- [`p25_daily_workflow_acceptance_20260710.md`](p25_daily_workflow_acceptance_20260710.md) — MA206 每日安全链验收（2026-07-10）
- [`p25_local_acceptance_20260710.md`](p25_local_acceptance_20260710.md) — P2.5 本地验收（2026-07-10）
- [`p25_vps_acceptance_20260710.md`](p25_vps_acceptance_20260710.md) — P2.5 VPS 公网验收（2026-07-10）
- [`p2_data_audit_report.md`](p2_data_audit_report.md) — P2-12 数据质量审计
- [`p2_eth_yearly_morphology_count_20260813.md`](p2_eth_yearly_morphology_count_20260813.md) — ETH 2026 同类空头形态计数（冻结门 v1）
- [`p2_l2_audit_and_prereg_20260803.md`](p2_l2_audit_and_prereg_20260803.md) — P2-L2 只读审计与预注册（训练前 Owner 门）
- [`p2_l2_preholdout_validation_20260803.md`](p2_l2_preholdout_validation_20260803.md) — P2-L2：immutable P1 dataset 训练与 pre-holdout 验收
- [`p2_local_signal_v2_early_frontier_review300_owner_result_20260812.md`](p2_local_signal_v2_early_frontier_review300_owner_result_20260812.md) — Local Signal V2 — 早期启动前沿 300 张 Owner 审核结果
- [`p2_local_signal_v2_early_frontier_review300_prereview_20260812.md`](p2_local_signal_v2_early_frontier_review300_prereview_20260812.md) — Local Signal V2 — 早期启动前沿 300 张 Owner 审核包 PRE-REVIEW
- [`p2_local_signal_v2_positive_semantic_audit_owner_result_20260812.md`](p2_local_signal_v2_positive_semantic_audit_owner_result_20260812.md) — Local Signal V2 语义审核结果：Positive基本成立，连续判别边界失败
- [`p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md`](p2_local_signal_v2_positive_semantic_audit_prereview_20260812.md) — Local Signal V2 Positive 语义纯度审计 PRE-REVIEW（2026-08-12）
- [`p2_local_signal_v2_positive_semantic_audit_prereview_v2_20260812.md`](p2_local_signal_v2_positive_semantic_audit_prereview_v2_20260812.md) — Local Signal V2 Positive 语义审核 PRE-REVIEW v2（2026-08-12）
- [`p2_local_signal_v2_semantic_boundary_diagnosis_20260812.md`](p2_local_signal_v2_semantic_boundary_diagnosis_20260812.md) — Local Signal V2：模型为什么把普通形态也认成正信号
- [`p2_owner_short_gold_center_hardneg_arm_20260811.md`](p2_owner_short_gold_center_hardneg_arm_20260811.md) — P2 Owner-short compact YOLO Hard-Negative第二训练臂（2026-08-11）
- [`p2_owner_short_gold_center_hardneg_canary_20260811.md`](p2_owner_short_gold_center_hardneg_canary_20260811.md) — P2 Owner-short Hard-Negative重训与连续密度Canary（2026-08-11）
- [`p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md`](p2_owner_short_gold_center_hardneg_canary_review331_report_20260811.md) — P2 Owner-short Hard-Negative Canary 331事件审核包（2026-08-11）
- [`p2_owner_short_gold_center_hardneg_r2_canary_20260812.md`](p2_owner_short_gold_center_hardneg_r2_canary_20260812.md) — P2 Owner确认误报第三训练臂与独立连续Canary（2026-08-12）
- [`p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md`](p2_owner_short_gold_center_hardneg_r2_dataset_audit_20260811.md) — P2 Owner确认误报第三训练臂数据审计（2026-08-11）
- [`p2_owner_short_hardneg_canary_owner_review_20260811.md`](p2_owner_short_hardneg_canary_owner_review_20260811.md) — Owner审核结论：当前模型约20%精确命中
- [`p2_owner_short_recent15d_core10_comparison_20260821.md`](p2_owner_short_recent15d_core10_comparison_20260821.md) — 两个Owner空头YOLO最近15天核心10币描述性扫描（2026-08-21）
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
- [`p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.md`](p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.md) — 15m 均线密集启动：因果 L1.5 + 多空 L2 全链路审计（2026-09-01）
- [`p3_15m_ma_launch_l1_l2_bypass_l15_20260901.md`](p3_15m_ma_launch_l1_l2_bypass_l15_20260901.md) — 15m 均线密集启动：移除 L1.5 后的 L1→L2 旁路验证
- [`p3_15m_ma_launch_l2_feature_addition_20260902.md`](p3_15m_ma_launch_l2_feature_addition_20260902.md) — 15m L2 因果特征增量实验（2026-09-02）
- [`p3_15m_ma_launch_l2_feature_group_ablation_20260902.md`](p3_15m_ma_launch_l2_feature_group_ablation_20260902.md) — 15m L2 28 特征分组消融（2026-09-02）
- [`p3_15m_ma_launch_l2_global_context_20260901.md`](p3_15m_ma_launch_l2_global_context_20260901.md) — 15m 均线密集启动：L2 全局上下文判断层 v1
- [`p3_15m_ma_launch_l2_reference_augmentation_20260902.md`](p3_15m_ma_launch_l2_reference_augmentation_20260902.md) — 15m L2 历史参考事件扩充实验（2026-09-02）
- [`p3_15m_ma_launch_l2_short_window_side_split_20260901.md`](p3_15m_ma_launch_l2_short_window_side_split_20260901.md) — 15m 均线密集启动：精确短窗 L2 多空回归审计（2026-09-01）
- [`p3_15m_ma_launch_l2_side_split_20260901.md`](p3_15m_ma_launch_l2_side_split_20260901.md) — 15m 均线密集启动：L2 多空拆分回归 v1
- [`p3_backtest_report.md`](p3_backtest_report.md) — 阶段 3 报告：事件驱动回测（第一轮）
- [`p3_ml_opt_backtest_compare.md`](p3_ml_opt_backtest_compare.md) — 回测对照：二分类 vs 回归收益（YOLO 主线池）
- [`p3_v11_pool_cutover.md`](p3_v11_pool_cutover.md) — p3 — v11 池判断层切换 ACTIVE
- [`p3_v8_pool_cutover.md`](p3_v8_pool_cutover.md) — p3 — 干净池(v8_chain)判断层切换 ACTIVE
- [`p3_yolo_mainline_backtest.md`](p3_yolo_mainline_backtest.md) — YOLO 主线整体回测（切流后，2026-07-15）
- [`p3_yoyo_dataset_v3_gold_core_prereview_20260812.md`](p3_yoyo_dataset_v3_gold_core_prereview_20260812.md) — yoyo-trading — Dataset V3 Gold Core 训练前验收
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
- [`p_model_inventory_20260820.md`](p_model_inventory_20260820.md) — 模型清单 — 我们到底训出了什么（2026-08-20）
- [`p_mtf_yolo_l2_bridge_prep_20260804.md`](p_mtf_yolo_l2_bridge_prep_20260804.md) — 小周期 YOLO → 冻结 L2 因果桥准备报告 — 2026-08-04
- [`p_oss_framework_survey_20260820.md`](p_oss_framework_survey_20260820.md) — 开源 AI 框架评估 —— 哪些真能帮到这个项目（2026-08-20）
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
- [`p_volatility_axes_20260818.md`](p_volatility_axes_20260818.md) — 波动率两条轴：owner「币波动越高越好」假说的匹配对照检验 — 2026-08-18
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
- [`week_plan_yolo_20260908.md`](week_plan_yolo_20260908.md) — YOLO 本周优化计划｜2026-09-08—09-13
