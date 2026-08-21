# 释放扩张能改善收益路径，但不等于提高入场质量

- **问题**：V13 用 `ATR[t] / trailing ATR mean` 与斜率一致性确认释放，很多普通延伸 bar 也会通过。改成 `TR[t] / ATR[t-1] >= 1` 且价格相对六线绳的方向距离继续扩大后，V14 final-preholdout 从 -11.29% 修复到 +8.30%，最大回撤从 23.07% 降到 12.96%。
- **死胡同**：看到总收益和回撤改善就宣布释放因子成功。V14 同段胜率仍只有 8.70%，24 小时内止损率反而升到 86.96%，匹配随机入场超额为 -108.70bp/笔，8 个匹配分配种子全部为负；改善来自更少交易和两笔长尾赢家，不是普遍更好的入场。
- **有效路径**：把“路径改善”和“选择能力”拆开验收：同时报告动态复利回报/回撤、短期止损率、匹配随机对照、top-decile 置换检验和多日赢家保留数。V14 可保留为未来判断层的 release 特征，但不能成为主 gate 或 production 候选。
- **通用规则**：释放过滤的最低证据不是净值曲线更平，而是短期止损率下降、匹配对照超额为正且跨时间块稳定；否则只记为风险路径组件，不记为 alpha 因子。
- **牵连**：`yoyo/layers/l2_judgment/pine_dense_release.py`、`scripts/research_pine_eth_15m_dense_release.py`、`experiments/active/exp-pine-eth-15m-dense-release-v2/`；V14 结果属于 analyst-exposed robustness evidence，不是 unseen OOS；repository holdout 未读取。
