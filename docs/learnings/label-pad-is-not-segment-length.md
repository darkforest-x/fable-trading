# 标签 pad 收紧 ≠ 解决「长密集段」

- **问题**：审计看到超宽绿框，第一反应常是「把 x_pad 加回来/加更大」或「换更大 YOLO」。
- **死胡同**：smoke3 把 `x_pad_px` 提到 12 抬了 mAP IoU 容错，却让 GT 变松；E1 收回 6 后
  宽均值只降 ~0.009（≈12px/图宽），框数不变——长横盘仍是一整段 dense segment。
- **有效路径**：单变量先改 pad 并 **原地 relabel**（`make_chart_transform`，不重渲图）；
  用 n_boxes 不变 + Δw≈2Δpad/W 验收。真要砍「长胖框」需改 segment 边界（merge/阈值），另开实验。
- **通用规则**：bbox padding 只平移/缩放边距，不重新定义对象；对象定义错了，pad 调再狠也只是修边。
- **牵连**：`src/detection/auto_label.py`、`scripts/relabel_yolo_dataset.py`、
  `analysis/p2a_e1_xpad_report.md`。
