# 先冻结待审核 ledger，再物化训练图片与标签

- **问题**：旧人工方向框、模型 hard negative、代码排序档位同时存在时，很容易把“可审核候选”提前写成“训练金标”；尤其 short 模型里的 Owner-long 空标签只表达“不是 short”，不能直接翻成 long 正例。
- **死胡同**：继续扫更多模型信号，或从 R1 的空标签目录复制文件，只会扩大一个尚未冻结的母池；在 Owner 过滤前就渲染图片和标签，也会制造一批看起来像正式数据、实际上仍缺样本级裁决和窗口哈希的产物。
- **有效路径**：回到 Owner 逐框方向事实，先按目标几何去重，再按重叠窗口依赖块做时间 split/purge；输出只有路径、边界、别名、排序档位和 `PENDING` 状态的候选 ledger。OHLC 窗口、图片和标签延迟到审核回执校验后物化，eligibility 全程 fail closed。
- **通用规则**：凡是“人工旧标 + 模型提议 + 代码预筛”的数据重建，第一产物必须是不可训练的 review ledger；只有 owner 回执、因果窗口 SHA、图片 SHA、标签 SHA 和 split 重验全部闭合后，才生成训练版本。
- **牵连**：`yoyo/datasets/owner_long_candidate.py`、`datasets/owner_long_gold_center_candidate_v1/`、holdout `2026-05-04`、150-bar purge、`training_eligible` Owner 保留门；另见 `protocol-confirmation-is-not-sample-confirmation.md` 与 `dynamic-recrop-does-not-repair-label-semantics.md`。
