# 盈亏平衡费率 f* 告诉你什么时候该停止做成本实验

- **问题**：BB×Stoch 每笔净 −0.147R，手续费占了亏损的一半（58 笔毛亏 −248U、费 −267U），
  很容易得出"先把成本压下来"的结论，然后花几轮去研究降频、放大目标、maker 单。
- **死胡同**：按几档费率重跑引擎做成本桥接。既慢又没必要——**费用不改变止损、止盈和成交价**，
  同一条冻结价格路径在任何费率下都成立，重跑只是把同一道算术算了四遍。
- **有效路径**：从已冻结的逐笔 fills 直接解一个数：`f* = Σ毛盈亏 / Σ成交名义`，
  即让总净额归零所需的每边费率。28 个月七组全部为负（−7.7 到 −22.7 bp）。
  **f\* 为负 = 免手续费也亏 = 成本不是病因**，所有"修成本"方向当场出局，不必再跑。
- **通用规则**：做任何成本或过滤实验之前，先算 f*。f* ≤ 0 就停手，问题在入场规则本身；
  f* > 0 才谈"要便宜到多少"。报告里把 0bp 那一列和 f* 一起给出来，不要只给当前费率下的净额。
  与 [`gross-edge-must-be-separated-from-cost-and-generalization.md`](gross-edge-must-be-separated-from-cost-and-generalization.md)
  同源，但那条讲怎么分解，这条给一个可以直接当停止条件的标量。
- **牵连**：`yoyo/evaluation/bb_stoch_longrun_study.py::fee_curve`、
  `experiments/active/exp-eth-bb-stoch-longrun-diagnosis-20260916-v1/results.json` 的 `fee_curves`。
