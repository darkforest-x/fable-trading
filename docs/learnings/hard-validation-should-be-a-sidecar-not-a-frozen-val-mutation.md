# 给冻结实验补 hard-val 应建 sidecar 而不是改写旧 val

- **问题**：已训练并登记哈希的数据集只有 easy-val，没有 hard-val；直接把 hard negatives 塞回旧 `images/val` 会让历史 mAP 的分母和数据身份静默变化。
- **死胡同**：把 train 的 hard 2:1 比例照搬到 val 在 BSB 同源时间块首先容量失败；即使降为 1:1，严格同币、时间切分、候选保护和旧负窗互斥后也只能安全得到 1,469/1,470。跨币或缩小保护区凑满会破坏标签契约。
- **有效路径**：保持原数据集逐字节不变，建立 evaluation-only sidecar；用原 val positive 的 W/C 几何作模板，排除全部候选 guard 与既有负窗，记录无法匹配的具体 positive。冻结同一模型和阈值，单独报告 FP/1000，而不重算或冒充原 mAP。
- **通用规则**：补验收面时先问“旧实验身份是否已冻结”；若已冻结，新样本只能作为带独立 manifest/hash 的 sidecar。比例是软目标，隔离、同源与时间边界是硬约束。
- **牵连**：`datasets/ma_launch_t3_10000_v1`、`datasets/ma_launch_t3_hardval_v1`、BSB 23 个 hard anchors、1,469/1,470、`docs/learnings/negative-ratio-must-not-weaken-gold-exclusion.md`。
