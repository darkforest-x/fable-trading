# 原始检测可视化必须保留前置过滤账本并写最终路径

- **问题**：ETH 30 日 YOLO 扫描的回执记录了 1,318 个 raw box，但落盘的 `accepted_candidates.csv` 只有 1,057 个结构合格框，41 张高清图又只展示 41 个 episode 代表框。要回答“所有原始检测”，不能把候选、去重事件或 episode 误当 raw 层。
- **死胡同**：直接复用现有 41 张图会静默丢掉 1,004 个滑窗重复候选和全部 261 个结构拒绝框；只凭 `raw_boxes` 总数也无法生成拒绝框的 `cx/cy/w/h`。另一个容易漏掉的错误是先在 `.building` 目录写绝对图片路径，原子改名后 manifest 仍会指向不存在的临时目录。
- **有效路径**：在不联网、不改模型/阈值/窗口的前提下，使用同一冻结 OHLC、权重、renderer、`conf/IoU/W18–25/batch/device` 重放缺失的 raw 坐标；把每个框先保留，再按源代码顺序套结构过滤，并用已发布的 1,057 候选逐条按窗口、类别、四坐标和置信度匹配，任何不一致都 fail-closed。渲染时每个 raw row 单独投影到 128-bar 全景并保存一框图；manifest 在临时树中预写最终目录路径，最后才原子改名。
- **通用规则**：扫描器只要报告了 pre-filter 数量，就必须同时保存 raw prediction ledger（至少含 source task、box index、`cx/cy/w/h`、confidence）；若历史产物缺失，只能用相同配置重放并以已发布后置层做 parity gate，不能从聚合事件反推“所有框”。所有原子构建产物的 manifest 都应记录 rename 后的最终绝对路径，并在发布后检查路径存在、尺寸、SHA 与一图一框不变量。
- **牵连**：`scripts/scan_15m_ma_launch_owner_yolo_eth30d.py` 的 `scan_totals` 与 `accepted_candidates.csv`；`scripts/export_15m_ma_launch_owner_yolo_eth30d_raw_detections.py`；`analysis/output/ma_launch_owner_yolo_eth30d_20260828_raw_detections_v1/`；原始层 1,318 → 结构候选 1,057（拒绝 261）→ 五根事件 53 → episode 41 的层级口径。
