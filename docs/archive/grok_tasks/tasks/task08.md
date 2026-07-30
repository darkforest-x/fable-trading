# Grok 整晚任务批次（无人监督·规格明确·不碰主线·全走 val 纪律）

## 铁律（违反=作废，你无权修改）
- 禁改 features.py 主线特征表、禁改冻结模型/frozen 配置、禁碰 data/forward_log.csv
- 禁评估 holdout（train.py 不加 --eval-holdout）、禁改 2026-05-04 后窗口任何参数
- 所有实验只用 train/val；无论好坏都写报告；负结果也是资产（照样入库）
- 每任务独立 commit push 到 grok/overnight 分支（不合 main，owner 晨审）
- judgment/factor/回测用系统 python3；YOLO 用 .venv/bin/python；OKX 请求已封装 UA
- 时间戳换算用 pd.Timedelta，禁 astype(int64)//1e9（见 docs/learnings/）
- 每个新实验脚本放 scripts/，报告放 analysis/，命名带假设号
- 判定用现有纪律：净@maker 与 baseline 比、置换检验 p<0.01、单特征 baseline 对照



# 你的本次唯一任务（做完就提交退出，不要做其他任务）
### 任务8：H8后续 30m深挖确认
30m池已知最强线索。用mtf_sweep在30m上跑TP{4,5,6}×horizon{48,60,72}完整网格，
确认h60是否稳定最优。→ analysis/p2b_h8_30m_grid.md（注意样本小，报告标注置信度）

完成后：git add 相关文件 → git commit → git push origin grok/overnight。
若卡住无法完成，在 grok_tasks/RESULTS.md 追加一行说明原因即可。你在 grok/overnight 分支的独立 worktree。