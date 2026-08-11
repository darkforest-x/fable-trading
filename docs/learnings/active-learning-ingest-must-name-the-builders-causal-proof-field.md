# 主动学习导入器必须显式声明构建器的因果证明字段

- **问题**：旧审核 manifest 用 `selection_future_used` 证明选样无未来，新时间块构建器改用更具体的 `hard_negative_newblocks_future_used`；通用导入器写死旧字段，导致 Owner 的完整 200 张裁决在落盘前安全失败。
- **死胡同**：在新 manifest 上补写旧别名会改变已冻结选择 SHA，使 Owner 导出的 `source_sha256` 失效；自动猜测所有以 `future_used` 结尾的字段又可能误选错误证据。
- **有效路径**：导入命令显式传入 `--causal-field`，逐行验证该字段存在且全为 false，并把字段名写入导入摘要；旧页面继续使用默认字段，已冻结产物不变。
- **通用规则**：跨协议导入器遇到安全证明字段时必须“显式命名 + 缺失即失败”，不得改历史产物补兼容，也不得靠模糊字段匹配猜测。
- **牵连**：`scripts/ingest_owner_short_train_hardneg_review.py`、`owner_short_train_hardneg_newblocks200_v3_20260811`、Owner source SHA、无前视质量门；失败发生在任何裁决文件写入之前。
