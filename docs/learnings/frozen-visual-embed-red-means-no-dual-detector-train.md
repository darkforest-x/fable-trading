# 冻结视觉 embed 预检红灯则不要开双检测器训

- **问题**：表格方向已死，想靠 long/short 双 YOLO 从像素学方向。
- **死胡同**：未过廉价门就上 3060 训双模；把「微调 backbone 可能不同」当成默认继续烧 GPU。
- **有效路径**：IT-14 用 tip 窗 + 冻结 COCO embed + LGBM；门=held-out AUC>0.55 或 top_dir_PF>1.3。
  结果红灯（AUC≤0.507，PF≤1.096）→ **默认不开**双检测器训练。
- **通用规则**：新模态上 3060 前先做冻结探针；正结果才升训，负结果强烈反对默认开训。
- **牵连**：`scripts/it14_visual_direction_precheck.py`；`analysis/p_it14_visual_direction_precheck.md`；
  [[dense-cluster-has-no-causally-tradeable-direction-edge]]
