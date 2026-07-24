# 同池上 binary 扩币/top-K 死、回归仍可出边

- **问题**：5×6m binary 镜像有极薄正净；Owner 要同窗扩到 ~30 常见币后再优化特征。
- **死胡同**：① 假设「扩 n 就能稳 binary 边」→ 30×6m binary 镜像 AUC≈0.52、净−0.181%、p≈0.13、`best_it=1`；② 再 top-K=10 更差。特征截断救不了错误目标函数。
- **有效路径**：同池改用 **regression + 分位筛单**（v11 哲学）立刻出发现级正净；镜像只作默认输入。扩币是样本闸，不是 binary 特征工程闸。
- **通用规则**：short 判断层主线先对齐目标（回归 vs 分类）；目标错了别堆 top-K/宇宙。
- **牵连**：`data/judgment_yolo_owner_side_short_30_6m.csv`；binary 报告 `analysis/p_short_judgment_refactor_v2.md`；回归报告 `analysis/p_short_judgment_reg_align_v11.md`。
