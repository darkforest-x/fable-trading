# 标杆数量必须从完整导出并集统计

- **问题**：当前正例审计把`data/benchmark_exemplars.json`中的176张注册标杆与Label Studio历史导出里的全部⭐标杆混为一谈，导致“当前正例只有70个精确标杆”的错误解读。完整导出并集实际有528个标杆stem、541个去重框；与方向复核表精确逐框联结后为188个short、91个long。
- **死胡同**：只读取`benchmark_exemplars.json`并把其与方向复核表的70个交集称为“全部精确标杆”。这个注册表是用于旧benchmark gate的冻结子集，不是所有历史⭐标注的权威全集。
- **有效路径**：从`output/label_studio/*.json`读取所有带`⭐标杆`选择的标注，按stem和归一化框坐标去重；再以逐框IoU≥0.999联结`analysis/output/owner_side_review/review_sheet.csv`，最后通过`owner_annotation_ids`回查实际训练manifest。
- **通用规则**：报告人工标签数量前必须先声明口径是“注册子集”还是“完整导出并集”；训练血缘核验必须追到annotation id，不能用注册表覆盖数替代实际入集数。
- **牵连**：`data/benchmark_exemplars.json`、`output/label_studio/*.json`、`analysis/output/owner_side_review/review_sheet.csv`、`datasets/owner_short_gold_center_v1/positive_manifest.jsonl`、`scripts/build_owner_short_gold_center_dataset.py`。
