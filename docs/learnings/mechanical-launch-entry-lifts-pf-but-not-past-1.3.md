# 机械「启动」入场抬 PF，但抬不过可交易线

- **问题**：Owner 指出先前 base rate 在「密集第 5 根/盘整中」入场，而他交易的是「启动」。需单变量分别测几种因果启动定义，相对 emergence 是否抬高 PF、能否过 1.3。
- **死胡同**：把「启动」与「方向全知 2.68」或「owner 框 oracle 1.18」混成同一尺子——前者是 TP3/SL1 事后选边天花板，后者是非因果标框右缘；都不能当作可部署启动规则的验收线。打包多种启动条件一次测会污染单变量归因。
- **有效路径**：统一下一开 + TP5/SL2 + 双成本；dense 门与已发表 emergence 对齐（judgment `add_indicators` spread，避免 `add_mas` 被覆盖的假对齐）；六行因果变体分开报。结果：emergence 复现 0.876；最好因果启动 `spread_expand_chg8` 到 **1.065**；突破/放量 ≈1.00–1.02；全部 <1.3，扣 0.2% 后全部 <1.0。
- **通用规则**：纠正入场时点（盘整中→启动）可以抠出约 **0.1–0.2 PF**，值得测一次；但机械突破/散开/均线交叉默认 **到不了 1.3**。过线证据仍只认可部署因果规则或前向新鲜样本，不认 oracle / 方向天花板。
- **牵连**：`scripts/launch_entry_base_rate.py`、`analysis/p_launch_entry_base_rate.md`（混池，已降权）、
  `analysis/p_launch_entry_long_short.md`（分边主裁决）、`analysis/p_base_rate_dense_verdict.md`、
  [多空必须分边](long-short-must-be-split-in-base-rate-tables.md)、
  [owner 框 oracle ≠ 因果 tip](owner-label-oracle-alpha-is-not-causal-tip-alpha.md)。
