# 毛收益的符号把"没有优势"和"优势扛不住成本"区分开

- **问题**：同一条 BB×Stoch 规则，ETH 5m 每笔净 −0.0992R、BTC 5m 每笔净 −0.0595R，
  都是亏的，很容易一句"这规则没用"带过。但它们的病因完全不同。
- **死胡同**：只看净收益。净额里成本和优势混在一起，两个都是负数的市场看起来一样，
  于是"该修什么"也就无从判断——有人会在没有优势的市场上继续压成本，也有人会在
  有微弱优势的市场上继续加过滤。
- **有效路径**：把 0 费率那一列单独拿出来看。ETH 28 个月毛 **−21.16R/650 笔**（免费也亏，
  规则本身负期望）；BTC 5m 毛 **+4.98R/697 笔**、BTC 1m 毛 **+11.74R/3159 笔**（有微弱正优势，
  被每笔 0.067R 的费用吃光）；XAU 1m 毛约 0。三种诊断，三种处理方式。
- **通用规则**：跨市场/跨周期比较时，**必报 0 费率毛收益与盈亏平衡费率 f\***，不要只报净额。
  毛为负 → 问题在入场规则，压成本和加过滤都是死路；毛微正 → 问题是优势量级，
  要么把 f\* 提到可获得费率之上（BTC 需要 <0.51bp/边，而最低 maker 2bp，做不到），
  要么放弃。见 [`breakeven-fee-rate-tells-you-when-to-stop-optimizing-cost.md`](breakeven-fee-rate-tells-you-when-to-stop-optimizing-cost.md)。
- **牵连**：`analysis/p1_btc_xau_bb_stoch_timeframes_20260916.md`、
  `experiments/active/exp-btc-xau-bb-stoch-timeframes-20260916-v1/*/results.json` 的 `fee_curves`。
  同一规则搬到新市场**没有重新调参**，因此比较的是规则而不是拟合结果。
