# 实际执行命令与耗时（RUN_ID v104-1h-incr-20260918-01）

所有命令在仓库根目录、main 分支、提交 ca0c536574（冻结计划 + runner + trace + 测试）之后执行；未 push。

| 步骤 | 命令 | 墙钟 |
|---|---|---|
| 测试 | `PYTHONPATH=. python3 -m pytest tests/evaluation/test_spike_v10_4.py tests/evaluation/test_spike_v10_4_increment.py -v` | ~2 s（25 项通过，见 test_log.txt） |
| 导出原提交（只读） | `git archive ccc52dff29 yoyo \| tar -x -C $SCRATCH/export_ccc52 && ln -s $REPO/data $SCRATCH/export_ccc52/data` | <1 s；yoyo 归档 sha256 c2b788b0… |
| 原代码复跑 1h | `PYTHONPATH=$SCRATCH/export_ccc52 python3 -W ignore experiments/active/exp-spike-v10-4-1h-increment-20260918-v1/repro_original.py --export $SCRATCH/export_ccc52 --out runs/v104-1h-incr-20260918-01/repro_original --workers 8` | 47.4 s |
| 三臂（严格拐点） | `PYTHONPATH=. python3 -W ignore -m yoyo.evaluation.spike_v10_4_increment --output runs/v104-1h-incr-20260918-01/strict --workers 8` | 99.4 s（含 638 个文件的边界预检） |
| 三臂（右侧可相等） | `... --output runs/v104-1h-incr-20260918-01/right_inclusive --workers 8 --pivot-ties right_inclusive` | 111.4 s |
| 统计/图/勘误 | `PYTHONPATH=. python3 -W ignore -m yoyo.evaluation.spike_v10_4_increment_report --run-root runs/v104-1h-incr-20260918-01 --out runs/v104-1h-incr-20260918-01/analysis` | ~20 s |
| 报告 | `python3 scripts/md_to_html.py <report.md> --out-dir analysis/html` | <1 s |

缓存与重算：三臂的全部信号、线、配对、交易、对照均**重新计算**；原 run_v1 的逐流 trades/joints/controls 只作为比对基准读取（sha256 见 plan 引用的 manifest）。
