# 跨越 holdout 边界的“最近窗口”必须给两个判定

- **问题**：用户要求回测最近半年 `2026-02-21～2026-08-21`，但仓库的 protected
  holdout 从 `2026-05-04` 才开始。只报完整半年会把已经看过的 2–4 月和真正受保护数据混成
  一个“最终验证”；只报 5 月以后又没有回答用户的半年问题。

- **死胡同**：用一个切片承担两种互相冲突的语义，或把完整半年里从 5 月开始截出的权益曲线
  当作独立 holdout。后者继承切点前的持仓、资金和 cooldown 状态，即使这次恰好没有跨界仓位，
  也不能预设每次都会相等。

- **有效路径**：在同一冻结配置、同一次 owner 授权中同时输出三个明确命名的口径：
  `requested_recent_6m` 回答用户的完整窗口；`protected_holdout_fresh_start` 从统一初始资金和空状态
  独立回放，承担正式受保护判定；`protected_holdout_continuous_state_diagnostic` 仅回答真实连续资金
  路径在切点后的表现。三者不能择优，也不能互相冒充。

- **通用规则**：任何用户窗口只要跨越训练/开发/holdout 边界，报告就必须把“业务问题窗口”和
  “独立证据窗口”拆开。主表可以并列，但成功门只能预先指定在独立证据窗口上；连续状态切片只能
  作运营诊断，并须报告跨界持仓数与切点权益。

- **牵连**：`scripts/backtest_pine_eth_15m_v12f_holdout1.py` 的 `PERIODS` 与
  `_continuous_protected_segment()`；`v12f_holdout1_recent6m_summary.json`；报告
  `analysis/p0_pine_eth_15m_v12f_holdout1_recent6m_20260821.md`。
