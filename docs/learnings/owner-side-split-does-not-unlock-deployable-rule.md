# 分边标框抬高的是 oracle，不是可部署因果规则

- **问题**：无 side 裁决已证明 owner 标框 oracle 有边、因果规则≈emergence。owner 再把
  同一批框标成 long/short 后，问分边能否挖出可部署增量（因果规则 PF@maker ≥ 1.3）。
- **死胡同**：①把分边后的 oracle PF（本轮 long 5.6 / short 7.4）当成「终于有边了」——
  side 与匹配结算同向 + 确认态标框，hindsight 被放大，不是新 alpha；②把 short 因果
  规则 1.127（高于 emergence 0.87）误读为「再调一点就能上线」——成功线是 1.3，且阈值
  来自本轮正样本分位，再抠易过拟合；③用 LGBM AUC≈0.97 当交易证据。
- **有效路径**：两边分别训披露 + 建 AND 因果规则 + 全市场 train 扫描；只看规则
  PF@maker。本轮 long 0.917、short 1.127，**均未过 1.3**。分边的真实信息是：手法语义
  仍是「方向结构（order_score）+ 已经在动（spread_chg8/ret）」，与 tip 出生无关；
  short 相对略好但仍是发现级确认态。
- **通用规则**：多空分边是**测量正确性**要求（混池会糊墙），不是 magically 解锁部署线
  的开关。裁决永远看**因果规则 base rate**；oracle / AUC 只解释手感从哪来。分边后
  oracle 暴涨而规则仍 <1.3 → 增量仍锁在 hindsight 选点，不写进可交易结论。
- **牵连**：`scripts/owner_side_feature_verdict.py`、
  `analysis/p_owner_side_feature_verdict.md`、
  [owner 标框 oracle ≠ tip 因果 alpha](owner-label-oracle-alpha-is-not-causal-tip-alpha.md)、
  [多空必须分边报](long-short-must-be-split-in-base-rate-tables.md)。
