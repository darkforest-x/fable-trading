# 只做空链路：池文件名必须带 side，规则池与 YOLO 池分流

- **问题**：Owner 选定 short-only 全链路（检测→判断→回测）时，判断层历史入口默认 long（`scan_candidates` + `label_candidate`），若直接复用 `judgment_dataset_v2_*` / `judgment_yolo_swap.csv` 会覆盖多边池或把空边障碍混进多边表。
- **死胡同**：等 YOLO `best.pt` 训完再动判断层——训练窗口被浪费；或在现有 long 脚本上「临时改默认」——违反池名纪律且难回滚。把混池 PF 当裁决会重蹈 `long-short-must-be-split-in-base-rate-tables`。
- **有效路径**：训练未完成时先铺 `--side short` 骨架：规则池走 `scan_short_candidates` + `label_short_candidate`，默认写出 `judgment_dataset_v2_{mode}_short.csv`；YOLO 池另文件 `judgment_yolo_owner_side_short.csv`，权重就绪后再扫。成功标准预注册为 tip-smoke/净收益+置换，不用 AUC，不碰 holdout/promote。
- **通用规则**：分边链路开工第一步 = 给数据集/tag/输出路径打上 side 池名；规则池可先验证管道，主链裁决仍以对应侧检测器候选为准。
- **牵连**：`src/judgment/build_dataset.py`；`scripts/yolo_candidate_source.py`；`analysis/p_short_only_pipeline.md`；`HANDOFF.md` 当前真相；相关 learnings `long-short-must-be-split-in-base-rate-tables.md`。
