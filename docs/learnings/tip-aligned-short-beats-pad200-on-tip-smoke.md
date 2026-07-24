# tip 对齐短金标过 tip-smoke；pad200 长链未过

- **问题**：空边检测要在盘口 tip 贴边开火；历史 pad200（v13–v15）tip-smoke 长期 **0/27**，而自家 val mAP 忽高忽低都曾被误读成裁决。
- **死胡同**：继续同构 pad200 / 只修 val 几何（H-DET-1/B）指望 tip-smoke 回升；或看到 tip 集 val mAP≈0.99 就准备 promote。
- **有效路径**：Owner short tip 集（右缘≈窗末 + 时间切分，无后文）训 `owner_side_short_tip_v1b` 后，同口径 `forward_log_vps_20260721` tip-smoke 得 **19/27**（live 对照 4/27）。发现级主指标是 tip-smoke，不是 mAP。
- **通用规则**：检测晋升门只认 tip-smoke / 真 tip 金标；val mAP 同几何时必然虚高，只能辅表。pad200「无后文」≠ tip 金标右缘语义，失败后勿再同构重训。
- **牵连**：`analysis/p_owner_side_short_tip_v1b.md`；`datasets/dense_owner_side_short_tip/`；`diag_tip_smoke_owner_side_short_tip_v1b.json`；对照 `p_v14_pad200_train.md` / `p_v15_tip_val.md`。
