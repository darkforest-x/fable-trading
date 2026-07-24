# 旧 pretip 窗上的框不能当 live tip 监督

- **问题**：short 集 sample30 上绿框右缘远离图最右红线（tip）；`box_right_frac` 中位≈0.52，却仍拿去训 `owner_side_short_v1`。
- **死胡同**：以为「框在中间 = Owner 故意标扩张中段」；或反过来只凭右缘分数否定意图——两者都混了**图像裁窗坐标**与**行情 tip 语义**。更糟的是直接 symlink `datasets/_deprecated_pretip/dense_owner_v11/` + 原样拷贝 `yolo_*`，等于用事后窗教 live 盘口检测。
- **有效路径**：先认几何事实——旧金标窗从未 tip 贴右，`build_owner_side_short_yolo.py` 不重裁；中段是坐标系问题。再查切分——承袭 sheet split 时 val 与 train 时间窗几乎全重叠（≈99%），违反时间切分。Owner 叫停续训；下一步只能 tip 重裁/重写框、贴 tip 子集、或重标，且按时间切开。
- **通用规则**：凡标注图来自 `_deprecated_pretip/`，默认**禁止**当 tip 检测训练集，除非先证明窗右缘=盘口 tip 且 train/val 时间不重叠。
- **牵连**：`scripts/build_owner_side_short_yolo.py`；`analysis/p_tip_mapping_owner_intent.md`；`analysis/output/owner_side_short_v1_sample30/`；纪律 12；另见 `box-right-frac-is-not-a-tip-intent-verdict.md`（意图 vs 几何拆开）。
