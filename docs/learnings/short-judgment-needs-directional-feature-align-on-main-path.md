# short 判断主路径必须做方向特征对齐，不能只换标签

- **问题**：tip_v1b short YOLO 候选已用 `label_short_candidate`，但判断层仍写入多头语义的 `ext_up` / `order_score` / `drawdown24` / `ret_*`，等于「空标签 + 多特征」。
- **死胡同**：只改标签/侧边 CLI、特征照抄 long `FEATURE_COLUMNS`——H10 learning 已警告却未进主路径；在 5×6m 上直接训出 AUC0.599 / 净+0.062%，看起来「有边」其实语义错位。
- **有效路径**：主路径统一 `extract_feature_rows_for_side(..., "short")`（`ext_up←ext_down` 等）；已有 CSV 用 remap 脚本只重算特征不重扫 YOLO；`train --side` 拒绝混边且 short tag 强制含 `short`。同池单变量对照：净 +0.062%→+0.156%，但 p 与 n=24 仍极脆。
- **通用规则**：short-only 判断层验收清单：① 标签函数是 short；② 方向特征已镜像；③ 表/tag/metrics 带 short 且无混边；缺一不可。
- **牵连**：`src/judgment/features.py`；`build_dataset.py`；`scripts/yolo_candidate_source.py`；`scripts/remap_yolo_short_features.py`；`train.py`；报告 `analysis/p_short_judgment_refactor_v1.md`；姊妹 [short-mirrors-need-directional-feature-semantics.md](short-mirrors-need-directional-feature-semantics.md)。
