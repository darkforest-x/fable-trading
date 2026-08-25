# 人工审核导出必须先变成锁谱系回执，不能直接变训练集

- **问题**：浏览器审核页能导出 KEEP/REMOVE/UNCERTAIN，但只有 `pack_id` 和答案不足以证明这些裁决仍对应当前训练清单、审核原图与排序账本；直接拿 JSON 删图会把页面状态误当数据契约。
- **死胡同**：只复用旧审核包的宽松 `summarize`，或只按 `review_id` 联结。这样无法发现 public manifest、训练正例 manifest、review-only truth 或 score ledger 被换过，也可能把含未来的审核图误接成模型输入。
- **有效路径**：汇总前逐一重算并核对四条已登记 SHA，再同时校验 `review_id + sample_id`、总量、重复答案、决策枚举和训练图片/标签 SHA；输出全量 joined ledger，未审行显式记 `PENDING`，review-only 图显式记为非模型输入，所有行继续 `training_eligible=false`。
- **通用规则**：人工页面的 JSON 只是输入证词。第一步永远先生成不可覆盖、fail-closed 的谱系回执；只有回执完整且 Owner 另行批准后，才能物化一个不覆盖旧版本的新数据集。
- **牵连**：`yoyo/datasets/ma_rope_review.py`、`tests/test_ma_rope_review.py`、`datasets/owner_short_gold_center_v1/positive_manifest.jsonl`；仍受 holdout 隔离、未来审核图物理隔离和 Owner 独占 `training_eligible` 裁决约束。
