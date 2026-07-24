# 预 holdout 短窗 + 少币 chunk resume 才能分钟级出 SHORT 首表

- **问题**：全宇宙/全历史 YOLO short 扫池估数十小时；Owner 要「最快」看到 SHORT ONLY 判断层第一张表。
- **死胡同**：① 全池 workers>1 易 Jetsam；② 10 币全历史仍太慢；③ HV 筛选本身要先算名单、且 Owner 已说不管 HV——再绕 HV 是浪费墙钟。
- **有效路径**：复用已有 `--months N --end-before` + `--symbols-file`；固定 5 流动性币 + `[end-6m, holdout)`；`CHUNK_SERIES=1` + `.done_symbols` resume。本轮 ≈20s/币、墙钟 ≈6min（含中断），训 <2s。
- **通用规则**：要「先看表」时，默认 **短窗 × 少币 × chunk=1 resume**，不要先上 HV/全宇宙；扫死了先看 done_symbols 再 resume，别清 CSV。
- **牵连**：`scripts/run_yolo_short_pool_chunked.sh`、`scripts/yolo_candidate_source.py`；产物 `data/judgment_yolo_owner_side_short_5_6m.csv`；报告 `analysis/p_short_only_backtest_tip_v1b_5_6m.md`。
