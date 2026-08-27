# 负样本增量扩容必须重验种子并钉住旧数据字节

- **问题**：把 10,000 个安全负样本扩成 30,000 个时，目标不是“重新抽一套 30,000”，而是保留 Owner 已接受的数据分布并新增 20,000。仅锁定旧 plan 的 SHA 不能证明旧窗口在新占用图、split 和特征实现下仍然安全，也不能证明新数据集没有改掉旧图片或标签。
- **死胡同**：按新的随机种子从头抽 30,000 会让旧 10,000 悄悄消失；直接信任旧 plan 后只抽新增窗口，又可能让新增窗口与旧负窗重叠。只说“同一个渲染器所以图片没变”也不够，因为像素、标签、样本集合是三条独立的复现轴。
- **有效路径**：把旧 plan 作为 slot 1 的 hash-pinned seed，但逐行重算 hard/easy mask、完整依赖 split、窗口几何和正候选禁入区；先把所有 seed 的隔离后区间重新写入 occupied mask，再选择 slot 2/3。构建后按 sample identity 对照旧 manifest，要求旧正例和旧负例的 image SHA 与 label SHA 全部一致，新增行另做不复用与区间互斥审计。
- **通用规则**：任何“旧数据集 + 增量样本”的新版本，第一步先定义不可变基线集合；规划时重验并占用基线，落盘后再做逐身份字节对照。配置比例是软目标，基线安全、Gold 隔离、时间切分和无复用仍是硬约束。
- **牵连**：`yoyo/datasets/ma_launch_owner_yolo_dataset.py`、`scripts/audit_15m_ma_launch_owner_yolo_neg30000.py`、`negative_sampling.seed_plan`、`lineage_baseline`、`pair_slot`；同时参见 [负样本比例不能弱化金标隔离](negative-ratio-must-not-weaken-gold-exclusion.md) 与 [复现必须逐轴验证](reproducibility-is-per-axis-not-a-boolean.md)。
