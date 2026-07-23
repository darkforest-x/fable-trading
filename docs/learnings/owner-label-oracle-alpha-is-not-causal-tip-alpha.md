# Owner 标框的 oracle 增量不是盘口 tip 的因果 alpha

- **问题**：audit 说密集几何 base rate 薄（emergence PF≈0.87），owner 坚信手动
  「完美密集」有 alpha。用 3k+ 有效 owner 框做因果特征 + LightGBM 披露，再用
  历史 base rate 裁判手法相对 emergence 有无增量。
- **死胡同**：①把框几何丢进「标框 vs 随机」LGBM → 负样本无框，AUC 直接 1.0（假可分）；
  ②把 LGBM / 高 AUC 当赚钱证据 → 标框右缘仅 1.7% 仍满足密集阈值，中位在窗宽 50%，
  第一特征是 `spread_chg8` 扩大（启动已在打印）；③假设 v17 真 tip 金标会继承
  owner 中段框的 PF 1.18 → 语义不同，不会自动继承。
- **有效路径**：分开报两数——**(a) oracle**：在 owner 框右缘 bar 直接入场的 train
  base rate（本轮 maker PF 1.183 vs emergence 0.874，有增量）；**(b) 可部署因果规则**：
  用 top 特征 AND 阈值全市场扫描（PF 0.869≈emergence，无增量）。只信 (b) 作「能不能
  上规则」；(a) 只证明手感对应的选点集合有边，且边来自确认态而非 tip。
- **通用规则**：手动标框 / 回看图上的「完美形态」默认是 **hindsight 确认态**；要先测
  「标框时刻是否仍满足出生条件」（密集阈值、窗内右缘位置、spread 变化方向），再决定
  下游是 tip 检测还是确认规则。**oracle 边 ≠ 因果可部署边**；AUC 再高也不交易 booster。
- **牵连**：`scripts/owner_label_feature_verdict.py`、
  `analysis/p_owner_label_feature_verdict.md`、
  `analysis/p_base_rate_dense_verdict.md`、
  [事后训练的判断层在盘口反预测](hindsight-trained-judgment-is-anti-predictive-at-the-tip.md)、
  [框右缘映射启动 bar 不是 tip](box-right-edge-maps-launch-bar-not-tip.md)。
