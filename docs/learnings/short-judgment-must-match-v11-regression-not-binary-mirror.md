# short 判断层必须回归同构，不能 binary 小样本拧镜像

- **问题**：Owner 要求 short tip 接判断层时，试点落到 binary 分类 + 5 币极小 n + 把 `feat_mirror` 当单变量胜负实验，偏离 ACTIVE≈v11 的「YOLO → 回归 LGBM → 分位筛单」主链。
- **死胡同**：在错误目标函数上拧特征语义（镜像 vs 不镜像）会制造「净收益变好」的假进步感，但 val top-decile n=24 极脆，且与实盘 ACTIVE 的 score 语义（预测 `realized_ret` / val-q90）不一致；`train.py` CLI 曾只有 binary 默认，更容易一路滑偏。
- **有效路径**：承认偏航 → 保留扩样本扫（30×6m）→ 主路径镜像当默认修债 → 显式 `--objective regression` 训 short → 报告以 top 分位净收益 / Spearman / val-q90 为主，AUC 降级为次要诊断。
- **通用规则**：凡「对齐主链」任务，先核对 ACTIVE frozen 的 `objective` / 阈值语义 / 候选规模哲学，再开训；镜像类方向债修完即默认，不当成下一轮优化旋钮。
- **牵连**：`models/ACTIVE`→`frozen_tp5_sl2_swap_yolo_v11_reg_*`；`src/judgment/train.py --objective`；`scripts/yolo_candidate_source.py --side short`；报告 `analysis/p_short_judgment_reg_align_v11.md`。
