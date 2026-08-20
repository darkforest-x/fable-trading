# 状态型入场 gate 必须在回放状态机内评分

- **问题**：对 V9 已执行交易表静态过滤，看似可以快速估算成交量门或 LR 的收益；但拒绝一笔入场会
  改变持仓、反转、退出和盈利后 cooldown，使后续可执行信号集合变化。成交量门静态估计 +50.50 bp/笔，
  动态回放只有 +41.22 bp/笔，入场 Jaccard 仅 84.52%。
- **死胡同**：只给基线 executed rows 打分，再取 top-decile。2023/24 完整 raw signal surface 有
  335 行，基线账本只有 166 行，覆盖 49.55%；缺失的 169 行会在之前的 gate 决策改变后变成候选，
  所以事后筛 CSV 连反事实样本空间都没覆盖。
- **有效路径**：先导出每个 guarded raw signal 的 28 个因果、side-aligned 特征，不放 outcome、score
  或阈值；未来模型必须对 scored period 全量候选逐行给出 next-open 前可用的分数，再把 pass 决策
  AND 到 long/short signal，最后运行原始 stop、BE、reverse、cooldown 和成本状态机。
- **通用规则**：凡策略包含持仓互斥、反转、cooldown、资金路径或动态仓位，entry gate 的验收单位是
  “重新生成的完整交易路径”，不是过滤旧 ledger。缺失、重复或迟到的分数一律 fail closed；阈值只能在
  更早 calibration 预注册。
- **牵连**：`scripts/analyze_pine_eth_15m_stateful_gate.py`、
  `scripts/prepare_pine_eth_15m_gate_surface.py`、`experiments/active/exp-pine-eth-15m-v1/judgment/`、
  P0/P1 `training_eligible=false`。
