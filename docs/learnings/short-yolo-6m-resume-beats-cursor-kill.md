# short YOLO 6m 扫用 resume+chunk，别绑 Cursor 会话

- **问题**：5 币×6m tip_v1b short 候选扫死在 1/5；Cursor/agent 会话结束会带走前台/弱 nohup 子进程。
- **死胡同**：单进程扫全名单、把 driver 挂在 agent shell 里「盯着」——会话一断进度归零或停在半币；MPS/多 worker 在 16GB 上更易 OOM。
- **有效路径**：`run_yolo_short_pool_chunked.sh` + `--resume` + `.done_symbols` + `CHUNK_SERIES=1`；6m/`--end-before 2026-05-04` 使单币约数十秒；必要时 launchd 续跑；扫完再 `--finalize` → train（无 holdout）。
- **通用规则**：凡「要过夜/要跨会话」的 YOLO 扫，第一步写 lock + done_symbols 续跑路径，再开扫；交付认 CSV/metrics，不认「进程刚才还在」。
- **牵连**：`scripts/run_yolo_short_pool_chunked.sh`、`scripts/yolo_candidate_source.py --resume/--chunk-series/--months/--end-before`、`analysis/output/SHORT_5_6M_PILOT.lock`、报告 `analysis/p_short_only_backtest_tip_v1b_5_6m.md`。
