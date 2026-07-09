# Exit variants must match current universe costs

- **问题**：H1/H2 出场变体已有离线原型结果，但原型混合全宇宙并沿用 spot maker 成本；主线已经切到 SWAP 后，继续引用旧结果会把宇宙和成本口径混在一起。
- **死胡同**：直接把旧 `exit_variants.json` 写进 R3 报告最省事，却无法回答当前真正问题：H1/H2 在 SWAP + 0.06% maker 成本下是否仍然优于 TP5/SL2。
- **有效路径**：复用原脚本和标签函数，但把扫描限定到 `_USDT_SWAP`，输出新产物 `exit_variants_swap.json`，并同时报告 top-decile 与 maker 组合 PF/maxDD。
- **通用规则**：任何发现级赢家在主线宇宙或成本模型改变后，都必须按当前主线口径重跑；历史结果只能作线索，不能作当前结论。
- **牵连**：`scripts/exit_variants_sweep.py`、`analysis/output/exit_variants_swap.json`、`analysis/p15_h1_h2_exit_report.md`。
