# Maker 试错桶必须隔离账本 + 环境门，禁止走主路径「加开关」

- **问题**：A2 要在 VPS 小仓验证 maker 入场，但主 `forward_log.csv` / executor 是 100 笔前向门与真金路径，一旦混写会污染裁决账本并可能误下单。
- **死胡同**：在 skeleton 里「复制主 log 最后 N 行打 trial 标记」——既不产生实时 tip 信号，又给人「已经在跑试错」的假安全感；往主 forward 加 `if trial` 分支则违背「主路径不变」且难审。
- **有效路径**：完全照 H1 shadow 先例——独立路径常量、独立入口函数、入口脚本双重门（`FABLE_MAKER_TRIAL=1` + kill 文件）、写前拒绝主/H1 路径、扫描复用 `scan_forward_records`、执行器另进程另授权。信号层与下单层分离：本脉冲只写 ledger。
- **通用规则**：任何「试错桶 / shadow / 纸面」新实验，默认三隔离：文件路径、进程/env 门、kill 开关；禁止改主 log 写者或三门新鲜度。
- **牵连**：`FORWARD_LOG_MAKER_TRIAL_PATH`；`run_forward_tracking_maker_trial`；`scripts/forward_maker_trial.py`；计划 `analysis/p_judgment_maker_trial_a2_plan.md`；对照 H1：`run_forward_tracking_h1_shadow`。
