# 入场前“已经扩张”会删掉从压缩中起步的大趋势

**日期**：2026-09-05  
**类型**：反直觉实验结论

- **问题**：开发期严格 ATR+BB 扩张门看起来能过滤盘整，因此冻结一个更宽的固定门：
  `min(ATR14/prior96 median, BB20 width/prior96 median) >= 0.85`。
- **死胡同**：把当前波动水平当成趋势释放质量。该门在 2023–2024 的 54/182 笔上由
  `-22.32` 改善为 `+4.59bp/笔`，但阈值来自已看过的 14 个敏感性组合，校正 `p=0.2756`。
  搬到 2025–2026-02 后，37/114 笔变成 `-42.97bp/笔、PF 0.475`，比匹配随机还差
  `-31.73bp/笔`。继续调 0.8/0.9 只会把 transport 段变成新训练集。
- **有效路径**：把趋势策略的右尾作为硬门。audit 最赚钱 12 笔中只有 1 笔通过，正收益只保留
  `6.34%`；未通过但 arm runner 的 31 笔平均 `+248.07bp`、horizon MFE `12.73 ATR`，通过且 arm
  的 14 笔只有 `+90.42bp`、`6.69 ATR`。这直接定位为**入场状态错误**，不是退出参数错误。
- **通用规则**：**“已经高波动”和“从压缩向有方向释放”是不同变量。** 趋势启动往往发生在带宽
  尚低、随后才扩张的状态；绝对扩张硬门容易追到冲击末端。下一候选应度量状态变化——K1 前压缩、
  K1 局部冲量、K2 回踩承接——并在新的前向数据上冻结验证。任何趋势入场门都必须先证明不会删除
  基线最强 10% 的大部分正收益。
- **牵连**：`scripts/research_ethusdtp_15m_expansion_confluence_v18.py`、
  `analysis/output/ethusdtp_15m_confluence_v17_v18/audit_top_decile_trades.csv`、
  `analysis/p1_ethusdtp_15m_causal_confluence_20260905.md`。相关：
  [波动水平与扩张是两条轴](volatility-level-and-volatility-expansion-are-opposite-axes.md)。
