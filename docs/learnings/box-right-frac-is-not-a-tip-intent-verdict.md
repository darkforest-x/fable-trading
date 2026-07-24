# 框右缘分数不能当「是不是 tip」的判决

- **问题**：Owner 主张框=tip；报告用 `box_right_frac` 中位≈0.50 暗示「标的是中段确认态」。
- **死胡同**：把存档 PNG 窗内几何直接当成行情 tip 语义；用图像坐标否定标注意图。
- **有效路径**：拆开两层——(A) `box_right_frac` 只描述裁图；(B) 在 `cut_global`（框右缘 bar）
  上测机械密集/扩张。本轮：A 中段属实但不冤枉意图；B 显示 cut 处 dense≈1.6%、
  `spread_chg8>0`≈98%、相对谷底偏晚≈10 bar → 机械 tip 缺口，应 remap/改阈值，不否定 Owner。
- **通用规则**：指标与意图矛盾时先审计映射；tip 对齐窗才会让右缘分数≈1.0。
- **牵连**：`scripts/tip_mapping_owner_intent_audit.py`；`analysis/p_tip_mapping_owner_intent.md`；
  IT-15 remap 诊断；勿与 `p_box_to_bar_lag`（模型滞后）混为一谈。
