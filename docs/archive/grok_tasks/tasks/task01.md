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
### 任务1：成交量因子三连（H14/H17/H18）
src/factors/library.py 追加因果无前视因子并跑 factor_ic_screen：
- obv_slope（20根OBV斜率）、vol_dryup（密集期量/前48均量）、
  taker_imbalance（(close-low)/(high-low)*volume 的20根均值近似买压）
判定：|IC|≥0.03且符号稳定→候选，报告注明待owner单变量验证。→ analysis/p2b_factor_ic_vol.md

完成后：git add 相关文件 → git commit → git push origin grok/overnight。
若卡住无法完成，在 grok_tasks/RESULTS.md 追加一行说明原因即可。你在 grok/overnight 分支的独立 worktree。