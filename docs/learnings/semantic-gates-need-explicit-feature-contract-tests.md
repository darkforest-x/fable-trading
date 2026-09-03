# 渲染特征齐全不代表语义门输入齐全

- **问题**：多周期扫描器复用了 YOLO 的 `build_tasks()`；它会补齐六条均线，所以模型渲染和结构框都能正常运行，但后置因果语义门在第一批结构框处才因缺少 `atr` 失败。
- **死胡同**：把“模型输入图能够成功生成”当成整条检测链的特征契约验证。渲染层只承诺均线，语义门还依赖父实验的 Pine-RMA ATR14；两个阶段共享同一张 DataFrame，却没有显式检查列集合。
- **有效路径**：在任务构建前复用父语义门的 `add_candidate_features()`，再由渲染器重算完全相同的均线，同时保留 `atr`；测试逐值比较父实现的 ATR，并验证其经过 `build_tasks()` 后仍存在且有限。
- **通用规则**：串联“模型提议 → 规则复核”时，先对每个阶段分别列出必需列，并写一条穿越阶段边界的契约测试；不要用上游成功来推断下游输入完整。
- **牵连**：`scripts/scan_crypto_grade_a_yolo_mtf_latest.py`、`scripts/verify_crypto_grade_a_yolo_mtf_latest.py`、`tests/test_scan_crypto_grade_a_yolo_mtf_latest.py`、`yoyo/layers/l1_detection/semantic_gate.py`；ATR 必须沿用父实验的 Pine-RMA14，不能临时换成滚动均值或另一个仓内 ATR 实现。
