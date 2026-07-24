# tip 重裁 + 时间切分才是 short 金标的最低可训条件

- **问题**：short v1 在 pretip 窗（`box_right_frac` 中位≈0.52）+ 承袭 sheet split（val 与 train 时间几乎全重叠）上开训，被 Owner 叫停。
- **死胡同**：symlink 旧 `dense_owner_v11` 图并原样拷贝 `yolo_*`——既不 tip 贴右，也不时间切分；或以为「只滤 ≥0.9 子集」能救，但仍可能继承坏切分/坏窗分布。另：多框同图常有不同 `cut_global`，不能共用一个 tip 窗。
- **有效路径**：按选项1 **一框一图**重渲——窗右缘=`cut_global`（全序列 `add_mas` 再切，对齐 live），框右缘强制贴 tip；train/val 用日历切点（本轮 `VAL_CUT=2026-02-01`，对齐 IT-16 p3），holdout 剔除；新目录不覆盖坏集；先抽 sample30 给 Owner 看图，**默认不开训**。
- **通用规则**：凡从 `_deprecated_pretip/` 出的金标要训 tip 检测，必须同时满足：(1) 重裁 tip 窗 + 重写框；(2) 按 `cut_time` 时间切分；(3) 验收 `box_right_frac` 右移且 train_max < val_min。缺任一条件 = 禁止开训。
- **牵连**：`scripts/build_owner_side_short_yolo_tip.py`；`datasets/dense_owner_side_short_tip/`；`analysis/output/owner_side_short_tip_sample30/`；姊妹坑 [pretip-window-boxes-are-not-live-tip-supervision.md](pretip-window-boxes-are-not-live-tip-supervision.md)。
