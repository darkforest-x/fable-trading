# tip 调度/阈值证伪后，下一刀必须是训练分布

- **问题**：实盘 tip_fire≈0，但离线 tip_hit 可以很高；容易误以为「少扫了 tip 窗」或「conf 太严」。
- **死胡同**：tip-only 调度、TIP_CONF 单独下调——tip-smoke 上 fired 仍 0/27，与 live 对照无差（见 `analysis/p_tip_only_smoke.md`）。A′ 贴边入账能挡事后账，但不过滤≠产生 tip。
- **有效路径**：把检测迭代写成单变量假设簇（H-DET），先登记已证伪项，唯一阻塞标成「等 pad200/无后文正样本训完 → tip-smoke+true_tip 对照」，禁止用 mAP 或离线 tip_hit 单独宣称成功。
- **通用规则**：tip 出生率问题先问「训练图有没有后文 / 验收是否只看右缘 N 根」；调度与阈值只有在出生率已 >0 时才有拧的价值。
- **牵连**：`docs/RESEARCH_AGENDA_DETECT.md`、`analysis/p_yolo_dense_hypotheses.md`、`scripts/eval_v13_vs_v12_tip.sh`；权重 `owner_v13_pad200`；门 `TIP_EDGE_BARS`
