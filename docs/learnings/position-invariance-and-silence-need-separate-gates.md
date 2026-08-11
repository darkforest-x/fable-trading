# Position invariance and detector silence need separate gates

- **问题**：真实裁剪 Stage A 的四个位置桶表现均衡，说明固定最右位置 shortcut 已消除；但同一模型在 easy negatives 上仍有明显误触。一个几何修复可以成功，同时模型安静度仍失败。
- **死胡同**：把“位置门通过”直接解释成“触发过多也修好”，或者在同一 val 上挑一个高阈值把误触压低。前者混淆了两个故障轴，后者会同时压垮召回，且同一 val 已参与 early stopping。
- **有效路径**：推理前冻结位置专属门——每桶最低召回、桶间召回差、anchor X 与 IoU-matched score 的秩相关；easy-negative fire 单独报告但不冒充连续市场密度。位置门只裁决能否作为因果 Stage B 初始化，安静度留给新模型自己的 hard negatives 和连续回放。
- **通用规则**：每个数据修复只用与其因果目标同构的门验收。位置随机化看跨位置稳定性；误报治理看独立难负例与连续暴露；阈值不能替代重训，也不能让一个门替另一个门背书。
- **牵连**：`scripts/eval_local_signal_v2_stagea_position.py`、`analysis/output/p1_local_signal_v2_stagea_position_eval_prereg_20260811.json`、Stage B 初始化裁决、后续 P2 hard-negative mining；holdout、ACTIVE、部署均不受影响。
