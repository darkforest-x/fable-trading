# 仓位乘数上线前必须先算 max_mult×基础仓位的保证金 ≤ 权益，否则高档位=必拒单丢单

- **问题**：tiered sizing（1x/1.5x/2x）获批上实盘，executor 侧只需「基础仓位 × tier 乘数」
  一行改动。但 VPS 实配 sizing_mode=equity_times_leverage、max_concurrent=1、leverage=3：
  基础仓位 = 权益×3 ≈ 277U，保证金恰好用满全部权益（92.4U）。任何 >1x 乘数的下单
  保证金需求都超过权益，OKX 必拒（51008，台账里 2026-07-16 有 5 次同码先例）。
- **死胡同**：直觉认为乘数是纯增量改动（「回测里 2x 赚更多，实盘照乘就行」）。
  实际回测口径是资本 10 单位、单笔 1x=资本的 10%，天然有 headroom；而实盘
  max_concurrent=1 的「基础」是满预算，两个口径的 1x 含义完全不同。更糟的是若照部署，
  q95+ 信号会以 2x 名义下单被拒成 order_failed，signal_key 进 taken 集不再重试——
  高分档交易整笔丢失，比不上线还差。
- **有效路径**：部署前先做保证金算术：max_mult × base_notional / leverage ≤ equity。
  不满足即停，报 owner 选口径（缩基础仓位到预算/max_mult、提杠杆、或充值），
  不自作主张缩水/加大（CLAUDE 升级规则）。代码与元数据可以先就绪并测试，部署单独闸门。
  **已落地（2026-07-21）**：owner 选口径①，见
  `tier-live-deploy-halves-slot-budget-not-leverage.md`。
- **通用规则**：任何「每笔金额 × 动态系数」的实盘改动，第一步用系数上界乘当前基础金额，
  对照交易所保证金/限额约束与真实台账里的历史拒单码；回测的 sizing 语义（相对什么的倍数）
  必须显式换算成实盘语义再比对。
- **牵连**：`src/execution/executor.py`（compute_entry_notional / signal_size_mult）、
  `data/executor_config.json`（VPS：equity_times_leverage, max_concurrent=1, leverage=3）、
  OKX 51008、`analysis/p_weight_centric_val.md`（capital 10 单位口径）、
  `docs/learnings/isotonic-sizing-collapses-rank-scores-to-steps.md`。
