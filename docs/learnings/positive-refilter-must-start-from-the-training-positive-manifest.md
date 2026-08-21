# 正例精筛必须从实际训练正例 manifest 开始

- **问题**：Owner 要优化旧训练集正例，却先后拿到 2,649 行混合 Gold 原图和“因果重绘 + 历史原图”双图页；页面都有完整谱系，但都没有回答“旧模型实际吃过的哪些正例应保留”。
- **死胡同**：把数据集谱系完整等同于审核母池正确。2,649 行含 1,402 个负例和六种审核面；统一重绘又改变了 Owner 当年看到的裁决表面，双图之间的窗口天然不一致。
- **有效路径**：从旧训练的 `positive_manifest.jsonl` 反推母池，得到实际 1,345 个正例，再逐条回连 Owner 当年的 `review_sheet.csv` 与绿色框预览；页面一次只显示一个目标，KEEP/REMOVE 直接回连训练 sample_id。
- **通用规则**：构建数据精筛页前先冻结四件事：实际训练 population、每次裁决的 unit、Owner 要看的原始 evidence、裁决将修改的新版本对象。任一项说不清就不能用“更统一的图”代替。
- **牵连**：`datasets/owner_short_gold_center_v1/positive_manifest.jsonl`、`analysis/output/owner_side_review/review_sheet.csv`、`yoyo/datasets/owner_positive_refilter.py`；长图可含未来但只能放在物理隔离的 review 目录，不能作为模型输入。
