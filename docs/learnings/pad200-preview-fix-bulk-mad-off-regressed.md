# pad200「修过又复发」：preview 修好 ≠ bulk 默认安全

- **问题**：Owner 记得昨天已修 stem 窗起点/窗末，今天 v13 抽查仍错框——不是记错，是 **两条路径默认不一致**。
- **死胡同**：以为改了 `resolve_win_start` 候选序（end_incl 优先）就全库安全；或用 preview 的 `mad=0` 对照当作 bulk 已正确。预览样本多为非 `okx_*`，遮住了混合约定。
- **有效路径**：查时间线——preview/初跑 MAD 开；同晚 `RESTART no-MAD` + `d2b2286` 把 MAD 改成默认关，bulk 盲 `end_incl` 毒死 `okx_*`（start）。「修过」只覆盖有存档 PNG 的路径；关门后 fallback 仍是单约定。
- **通用规则**：消歧门从「默认开」改成「为省资源默认关」= 换了另一条生产路径。合并前必须对照 `pad200_summary.json` 的 `mad_gate`，禁止用 preview 成败推断 bulk。姊妹坑双向都致命。
- **牵连**：`logs/v13_pad200_pipeline.log`（`RESTART no-MAD`）；`d2b2286`→`bdde170`；`datasets/dense_owner_v13_pad200/pad200_summary.json`；`analysis/p_pad200_regression_why.md`；[stem-index-is-window-end-not-start.md](stem-index-is-window-end-not-start.md)；[pad200-mad-gate-off-corrupts-okx-start-stems.md](pad200-mad-gate-off-corrupts-okx-start-stems.md)。
