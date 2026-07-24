# Owner 子集上的 tip 前移不是可部署边

- **问题**：把 Owner 框右缘前移到局部密度谷后，walk-forward raw PF 可抬到 1.5–2.5。
- **死胡同**：把「事后被选中的事件上回到更早点」写成可交易 tip alpha；相信同集上的 LGBM top PF（可到 10–30）。
- **有效路径**：承认这是选择偏差诊断（证明 cut 偏晚），部署边必须来自**无 Owner 挑选的机械全市场扫描**（emergence 历史 ~0.87 才是对照）。
- **通用规则**：oracle/选中子集上的时间机器 ≠ 盘口因果边；报 raw 时必须写清样本门。
- **牵连**：`scripts/it15_tip_remap.py`；`analysis/p_it15_tip_remap.md`；
  [[owner-label-oracle-alpha-is-not-causal-tip-alpha]]
