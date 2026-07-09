# Funding Cost Reruns Need Input Snapshots

- **问题**：P1-7 要把 SWAP 回测中的资金费近似替换为真实 `realizedRate`，但复跑
  `swap_replication.py` 时 top-decile 毛利也明显变化，容易把输入池变化误归因给资金费。
- **死胡同**：只看新旧 maker 净收益差，会把“当前本机数据缓存重建后的候选/切分变化”
  和“资金费成本模型变化”混在一起；这会污染对 TP5/SL2 的经济性判断。
- **有效路径**：同时保留旧 `maker0.06%` 近似列、真实资金费覆盖率、covered 子集净值和
  real-vs-approx delta；报告里明确说明真实资金费本身只带来约 bp 级改善，经济性变弱来自
  复跑输入数据池变化。
- **通用规则**：任何成本模型复核都先固定或记录输入快照；无法固定时，必须把输入变化和
  成本变化拆成两条结论，不得用单个新净值覆盖旧结论。
- **牵连**：`scripts/swap_replication.py`、`src/data/funding.py`、
  `analysis/output/swap_replication.json`、`data/funding/`、`data/swap_replication/`。
