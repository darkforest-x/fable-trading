# 扩充标注前先找回人工标签的来源链

- **问题**：当前 Grade-A 数据集被完整导入为空白任务后，Owner 提醒历史上已审核一万多张、留下千余正例和数百标杆。新任务数量并不代表历史人工资产的规模。
- **死胡同**：只围绕当前 32,000 张派生图安排 4,172 个事件全量重画，容易重复劳动；把当前自动扩充的 1,043 个正事件误认成旧人工筛出的 1,345 张，则会同时丢失人工依据、抬高机器标签的可信度。
- **有效路径**：先区分 task、图、框、事件与裁剪变体，再联结原 Label Studio 框、Owner 方向裁决、⭐身份和派生核心。原人工身份保留；几何派生只继承来源，不冒领逐框人工确认。当前方向表与旧报告的 SHA 一致，1,345 正例 manifest 的 SHA 也一致，证明关键资产仍在，而非只有历史叙述。
- **通用规则**：重标队列先做已有标注覆盖和冲突清点。可精确复用的保留，只把语义变化、派生边界、缺失和新增样本排给人工；机器预筛等级不能充当人工金标，未入选正例不能自动变成负例。
- **牵连**：`analysis/output/owner_side_review/review_sheet.csv`、`datasets/owner_short_gold_center_v1/positive_manifest.jsonl`、`data/benchmark_exemplars.json`、Label Studio 项目 76/74；见 [原始金标几何优先](original-gold-geometry-beats-secondary-manual-reboxing.md) 和 [协议认可不等于逐样本认可](protocol-confirmation-is-not-sample-confirmation.md)。原框到当前 4/5 根核心的适用性仍待逐项核对，不自动修改训练标签或评估资格。
