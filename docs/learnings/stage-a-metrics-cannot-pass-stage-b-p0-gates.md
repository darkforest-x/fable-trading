# Stage A 训练指标过不了 Stage B 的 P0 门

- **问题**：交接规范要求生产训练图 `visible_end ≤ decision`、时间切分、训练集无 holdout；现成 w20 midbox（Stage A）val F1 看起来可训，但七道硬门槛只过 3 道。
- **死胡同**：在 Stage A 数据集上继续 hardneg / tip 回测并指望「指标变好就等于可实盘」——中位仍有 9 根未来 K，val 与 tip smoke 落差会重演 future-dependency 曲线。
- **有效路径**：另建 Stage B 数据集（不覆盖 Stage A）：窗口右端钉 decision、时间序 + purge、硬过滤 holdout、manifest 守恒；用同一套 gate 脚本机器判定 `p0_pass` 后再开训。
- **通用规则**：位置随机预训练（Stage A）与因果生产数据（Stage B）必须分目录、分验收；任何「只有 Stage A」的 mAP/F1 不得进 P1 全量叙事。
- **牵连**：`scripts/build_local_signal_v2_stageb.py`、`scripts/audit_local_signal_v2.py`、`datasets/local_signal_v2_stageb`、规范 §3/§14/§16.1。
