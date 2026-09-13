# 广度报告必须同时固定阶段一与对照生成器身份

- **问题**：匹配对照报告原先只核验 matched-controls 生成器；阶段一 summary 即使由已变更的研究代码生成，也可能被当作当前结论的输入。
- **死胡同**：只核验 CSV、候选账本和对照生成器的 SHA，仍无法证明阶段一 `outcome_summary.csv`、`frozen_candidate_rule.csv`、`single_variable_slices.csv` 对应当前的阶段一构建语义。
- **有效路径**：把 stage manifest 的 `study_code_sha256` 作为与 matched manifest 同等级的 fail-closed 契约，先验证其 64 位 SHA-256 格式，再与当前 `spike_market_breadth_study.py` 字节哈希逐字节比对；报告仅解析已核验的 summary/manifest，pairs 与 receipts 始终只做字节核验。
- **通用规则**：整合两个独立阶段的研究产物时，每个会影响被展示 summary 的生成器都必须有当前代码身份 pin；只 pin 下游汇总器不能证明上游统计的语义未漂移。
- **牵连**：`yoyo/evaluation/spike_market_breadth_report.py`、`tests/evaluation/test_spike_market_breadth_report.py`、stage/matched `manifest.json`。
