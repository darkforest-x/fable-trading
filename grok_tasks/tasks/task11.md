# 任务11：主线前向健康诊断 + 旁路回放

## 铁律摘要
- 禁改 ACTIVE / frozen_tp5_sl2_swap_20260709  
- 禁 holdout；禁改阈值/TP/SL  
- **禁止 truncate `data/forward_log.csv`**  
- 回放输出必须写到旁路文件，默认：`data/forward_log_mainline_replay.csv`

## 背景（已核实 2026-07-20）
- `data/forward_log.csv` 仅表头，n=0  
- 早期备份 `forward_log_rules_pre_yolo_20260715.csv` 有 7 笔 closed，但混入 NFLX/QQQ/ORCL/EWJ（stockish）  
- K 线末 bar 约停在 2026-07-16；日链 Claude 任务已停用  

## 做什么

1. 写只读诊断脚本 `scripts/forward_health_report.py`（或扩展 `daily_digest` 的 dry 路径）：
   - 各 `data/forward_log*.csv` 的 n_open/n_closed/n_maker、模型路径、阈值、时间窗  
   - stockish 占比、BLOCKED 命中数  
   - ACTIVE 与日志 `model_path` / `dataset_sha256` 是否一致  
   - K 线最新 bar 与「今天」的滞后小时数（抽样 BTC/ETH）  
2. **旁路回放**（需网络拉数时：先检查 `data/kline_fetched` 是否过旧；可先 `python3 -m src.data.update_okx`，失败则记 RESULTS 并跳过回放）：
   ```bash
   PYTHONPATH=. python3 scripts/forward_track.py \
     --out data/forward_log_mainline_replay.csv
   ```
   （确认 CLI 已支持 `--out`；默认 start 用代码内 `FORWARD_START`，**不要**擅自改正式 start）  
3. 报告对比：replay 全量 vs replay **crypto-only**（若 task12 未完成，先用 `is_stockish` 过滤 symbol 做表后统计）  
4. 写 `analysis/p_fwd_health_20260720.md`，必须含：
   - 复现命令  
   - 数据/日志统计表  
   - 与 07-10 纪要的差异（为何主文件变空：如实写「未知/疑似实验覆盖」，勿编造）  
   - **风险与诚实声明**  
   - 下一步选项（标注哪些需 owner 决策：是否把 replay 晋升为 `forward_log.csv`）

## 不做
- 不 `cp` 覆盖主 `forward_log.csv`  
- 不改 dashboard 默认读路径（除非只加只读展示旁路，且报告说明）  
- 不评估 holdout  

## 完成定义
- 报告落盘 + 若回放成功则旁路 CSV 非空  
- `git add` 脚本/报告/测试（若有）→ commit → push `origin grok/overnight`  
- 在 `grok_tasks/RESULTS_v2.md` 追加本任务段  
