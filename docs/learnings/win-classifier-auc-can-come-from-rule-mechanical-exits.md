# 胜率分类器的 AUC 可能来自规则本身的“入场即离场”，不等于会挑赚钱单

- **问题**：信号中心云带信号评分（exp-signal-center-score-20261106-v1）里，1H 模型时间外 AUC 0.64 / 0.69、top 20% 胜率从 21% 升到 32% / 37%，看上去很强；但 2025 年 top 20% 扣费后仍是 −54bp/笔，判失败。
- **死胡同**：起初按 CLAUDE.md 的“AUC 反常高先查泄漏”去怀疑特征看了未来。截断测试（删掉信号之后的 K 线，特征逐字节不变）排除了前视，说明问题不在泄漏，而在标签结构。
- **有效路径**：出场规则是“下一根的 EMA 交叉价若已越过入场收盘价，就在该收盘离场”。这个结果由信号 K 线收盘时已知的 EMA 状态直接决定，固定亏损约 10bp 手续费。1H 有 12% 的信号属于这种情况，模型学会避开它们（top 20% 里只剩 2–3%），AUC 和胜率因此抬高。剔除这类单子后 AUC 降到 0.58 / 0.64；按扣费后均值看，排序价值只剩一年为正。
- **通用规则**：给规则型交易训练胜负分类器前，先统计出场原因。凡是在信号时刻就已确定的离场（立即止损、立即反向、无法成交），都要单独报占比，并报剔除后的 AUC 和胜率。成功与否只看 top 组扣费后的净收益与分层置换，不看 AUC，也不看胜率。
- **牵连**：`yoyo/evaluation/signal_center_score_model.py` 的 `diagnostics`（immediate_exit_*、auc_excluding_immediate）；`yoyo/layers/l3_backtest/nautilus_stop_schedule.py` 的 `flip_at_close`；另见 [fat-tailed-trend-exits-need-ex-tail-and-median-checks.md](fat-tailed-trend-exits-need-ex-tail-and-median-checks.md)。
