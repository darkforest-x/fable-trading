# 固定门失败时先用阈值无关排序诊断定责

- **问题**：P2 的 calibration q90 在部分折落入大并列块，fixed gate 放行率严重漂移；表面上很容易把经济失败归因于 threshold。
- **死胡同**：只盯 fixed-gate pass rate 或尝试改 q90，会把“门的传输失败”和“排序本身没有增益”混在一起；门调得更窄也可能只是从同一坏排序里少选一些。
- **有效路径**：先在每个独立 fold 内计算 exact top-decile，再与该折整池经济基线和匹配对照比较。P2-R 中 exact-top 4/5 折为负、加权后比整池还差 0.59bp，匹配 lift 仅 +0.74bp 且 p=0.4836，因此可以在不试新 threshold 的前提下排除“只改门即可修复”。
- **通用规则**：遇到 selector gate 失效，第一步先用 fold-local、阈值无关且不跨模型 pooling 的排序诊断；只有排序增益先成立，才有资格讨论固定门校准。
- **牵连**：`analysis/output/p2r_root_cause_audit_20260803.json`、`analysis/output/p2r_fold_diagnostics_20260803.csv`、`docs/learnings/walkforward-model-scores-must-not-be-pooled-across-folds.md`。
