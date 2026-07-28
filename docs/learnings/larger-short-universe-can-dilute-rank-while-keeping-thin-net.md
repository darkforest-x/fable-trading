# 扩 short 宇宙可抬厚 top-n 净，却稀释排序（ρ 塌）

- **问题**：30×6m short 回归单切净 +0.37%、Spearman≈0.15；Owner 要同构扩到 ~100 币逼近 v11 候选量级，检验边是否稳住。
- **死胡同**：① 把「候选 n 接近 v11」当成稳健门 → 100×6m n=2.56 万后单切净仍 +0.47%，看似进步；② 只看 net_mean 仍正就想进障碍/holdout → 忽略 Spearman 从 0.15 塌到 0.016、walkforward ρ_mean 转负、多折 best_iteration=1。
- **有效路径**：单切与 walkforward **同表对照 30 池**：净可同号略升，但排序诊断全面变差 → 判为「厚度够了的间歇/弱边」，按 S2 决策树停扩币，回检测金标（S3），不 promote。
- **通用规则**：扩宇宙的成功标准不是 n 变大或单切净仍正，而是 **排序（ρ / 折间稳定性）是否同步改善**；ρ 塌而净略正时，优先怀疑稀释/伪过滤，而不是继续加币。
- **牵连**：`data/judgment_yolo_owner_side_short_100_6m.csv`；`analysis/p_short_judgment_100_6m_reg.md`；`analysis/p_short_judgment_100_6m_reg_walkforward.md`；对照 `..._30_6m_*`；计划 `analysis/project_management_plan_20260724.md` S2 分支 2。
