# 方向反类的空标签不能翻成另一方向正例

- **问题**：short-only 检测器把 Owner 明确判为 long 的相似结构作为 hard negative，因而保存了图片、原框派生 core 元数据和空 YOLO 标签。后来要做 long-only 检测器时，这批文件看起来像现成多头数据，极易被直接复用。
- **死胡同**：复制这批图片、改目录名或把 `owner_semantic_verdict=long` 当成已有正标签。空 `.txt` 只表达“short 类中没有框”；core 元数据也只是派生审计信息，不会自动变成经确认的 long YOLO 几何。这样做会把多头正例继续训练成背景，或者把未确认边界冒充金标。
- **有效路径**：回到同一条 Owner 原始方向裁决、原始 box id 和精确 OHLC 源，重新派生 long 正框；逐条记录方向确认层级与核心边界确认层级。原 short 人工框可以反向作为 long-only 的方向 hard negative，但也必须从原事实重建空标签并重新经过时间范围、保护区、去重与 split 检查。
- **通用规则**：切换单方向检测器的目标方向时，第一步审计“标签文件表达的是哪一个类空间”。任何 opposite-side hard negative 都只能证明它不是当前类，不能靠重命名变成另一类正例；正例必须从原始语义事实与几何事实重建。
- **牵连**：`analysis/output/owner_side_review/review_sheet.csv`、`datasets/owner_short_gold_center_hardneg_candidates_r1/owner_long_manifest.jsonl`、`datasets/owner_short_gold_center_hardneg_r1/hard_negative_manifest.jsonl`、`scripts/build_owner_short_gold_center_hardneg.py`、`scripts/audit_owner_long_gold_center_plan.py`；同时受时间切分、holdout 隔离、原始几何优先和 `training_eligible=false` 约束。
