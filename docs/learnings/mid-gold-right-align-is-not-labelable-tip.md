# 中段金标右对齐 ≠ 可标的 tip 密集框

- **问题**：pad200（切框右缘 + 左补满 200）把历史中段金标 remap 到右缘后，Owner 说「不可能标这种框」。
- **死胡同**：以为只是几何「框靠右了所以像 tip」就能当 tip 训练 GT；水平 remap（bar 跨度）按协议是对的，但两张预览上 **价格框与 tip 均线束重叠率为 0**（框落在均线下方），且左补后窗口重算 MA 使 tip `full_spread` 往往不再满足密集规则（LINK 原窗 0.001 → tip 窗 0.012）。
- **有效路径**：实盘 tip 几何要重新渲染——200 窗、右缘=当前、无后文、**空 label**，由 Owner 在右缘人工打标；不要把中段金标 remap 画成真相。
- **通用规则**：删后文 / 右对齐历史框之前，先量化「remap 后框是否仍覆盖当前窗的 MA bundle」；覆盖率为 0 就停，改走空标 tip 包。
- **牵连**：`scripts/build_crop_pad200_dataset.py`；对照 `analysis/output/v13_tiponly_preview/pad200_after_box_*`；正确打标包 `datasets/label_live_tip_1000/` + `scripts/make_live_tip_label_pack.py`；相关 `docs/learnings/crop-after-box-must-left-pad-to-200.md`。
