# tip 窗像素（冻结 COCO embed）也不携带可交易方向边

- **问题**：Owner 想用双检测器吃「视觉 gestalt」方向；IT-14 用 tip 右缘窗图 + 冻结
  yolo11n COCO embed + LightGBM，在同池对照 130 表特征，问像素是否多出方向信号。
- **死胡同**：把「外观方向 AUC 高 / 人眼能看」直接当成可训双检测器的理由——IT-02 表方向
  已是硬币，视觉若不做因果门就会烧 3060。
- **有效路径**：廉价预检门 = walk-forward visual AUC>0.55 或 top_dir_PF>1.3 才升 3060。
  实测 VIS AUC≤0.507、PF≤1.096（最近期 0.65），与 tabular 同塌 → **红灯，不开训**。
- **通用规则**：任何「换视觉 backbone / 双检测器」默认先跑冻结 embed 因果探针；负结果
  强烈反对默认开训（正才升级）。训练一律 3060，本机只做探针。
- **牵连**：`scripts/it14_visual_direction_precheck.py`、
  `analysis/p_it14_visual_direction_precheck.md`、`p_judgment_layer_lab.md` §7、
  IT-02 / HANDOFF「训练默认 3060」
