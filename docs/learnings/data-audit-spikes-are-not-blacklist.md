# 数据审计：偶发 tip 尖刺 ≠ 黑名单

- **问题**：P2-12 初版把「任意 1 根 15m |ret|>25%」算 structural，SWAP 15m
  大半山寨都被标 spikes，黑名单候选失去区分度。
- **死胡同**：把 crypto 正常插针当成坏数据剔除 → 误伤主线币池。
- **有效路径**：CSV 仍记录 `spike_bars` 全量；structural 用 ≥3 根，黑名单用 ≥8 根
  或 zero_vol>5% / 真缺口 / OHLC 坏；股票类薄流动性另表（zero_vol 阈值更严）。
- **通用规则**：质量审计要分「观测指标」和「排除门槛」两层，排除门槛必须按资产
  类别校准，否则报告只有噪声。
- **牵连**：`scripts/data_audit.py`、`analysis/p2_data_audit_report.md`。
