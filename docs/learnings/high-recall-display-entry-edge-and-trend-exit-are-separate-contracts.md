# 高召回显示、入场优势与趋势退出是三份合同

- **问题**：15m 明显启动需要尽量在图上被识别，同时赢家又应沿均线持有。把“看得到”“值得进”和“进场后怎样退出”揉成同一套阈值，会让高召回被误当成可交易优势。
- **死胡同**：先换 SMA40/60、去重方式、固定 TP、均线退出与利润保护，再让 L2 从 broad pool 里挑高分。均线 runner 的确放大最大赢家，但全池和 L2 子集扣 20bp 后仍负；继续收紧保本线只减少红单数，却截断正常回踩。原因是多数交易在 runner 激活前已经失败，退出规则没有机会起作用。
- **有效路径**：锁死同一批 next-open 入场，分别比较固定 TP、预注册 MA runner 与显示版 runner；再按“激活前失败 / 激活后反转 / 激活赢家”分解收益贡献。只有右尾变大而均值仍负时，结论只能是退出机制有效、入场合同无效。显示层继续高召回，交易层另建真实 K1→K2 episode，退出层暂时固定，避免归因混杂。
- **通用规则**：看到“抓到几笔大趋势”时，先问最大赢家、p99 和总体净均值分别由哪一层产生。只有同一入场池的退出配对比较能评价 runner；只有时间外净收益与匹配随机对照能评价入场。显示召回不得直接触发真实仓位。
- **牵连**：`scripts/research_btcusdtp_15m_ma_runner_grid.py`、`scripts/build_btcusdtp_15m_trend_refactor_report.py`、`experiments/active/exp-btcusdtp-15m-high-recall-l2-trend-runner-preholdout-20260904-v1/pine/fable_15m_trend_research_v2.pine`；固定成本 0.2%，holdout 起点 2026-05-04，Pine 保持 research-only。
