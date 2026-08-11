# Post-val Owner负例是参考尺，不是可直接训练的行

- **问题**：Owner在独立post-val canary中确认了大量高价值误报，很容易顺手把这些图片回流训练；但当前冻结train早于val，而canary又晚于val。
- **死胡同**：保持旧val不变，却把canary误报加入下一轮训练。图片虽然各自因果，整个实验的时间顺序却变成`train → val → 新train`，旧val分数不再是样本外证据；把它称为“只加hard negatives”会掩盖评估污染。
- **有效路径**：冻结post-val裁决为错误语义参考，全部保持`training_eligible=false`；用它定义误报类型和相似度检索方向，再到原train截止时间内挖同类候选并重新确认。第三臂只替换train-time负例，val和连续canary继续独立。
- **通用规则**：任何人工审核结果准备回流训练前，第一步同时比较该行时间、冻结train末端和冻结val末端。无前视是单样本条件，时间切分是实验条件；满足前者不能代替后者。
- **牵连**：`scripts/ingest_owner_short_canary_review.py`、`datasets/owner_short_gold_center_v1/positive_manifest.jsonl`、`analysis/output/owner_short_gold_center_hardneg_canary_review331_v3/owner_review_labeled_manifest.jsonl`；受时间切分、单变量和holdout纪律约束。
