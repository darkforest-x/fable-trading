# Grok 整晚任务批次（无人监督，规格明确，不碰主线）

## 铁律（违反=作废，你无权修改这些）
- 禁改 features.py 主线特征表、禁改冻结模型/frozen 配置、禁碰 data/forward_log.csv
- 禁评估 holdout（train.py 不加 --eval-holdout）、禁改 2026-05-04 后窗口任何参数
- 所有实验只用 train/val；结果无论好坏都写报告；负结果也是资产
- 每个任务独立 commit，push 到 grok/overnight 分支（不合 main，owner 晨审）
- 环境：judgment/factor 用系统 python3，YOLO 用 .venv/bin/python；OKX 请求已封装 UA
- 时间戳换算用 pd.Timedelta，禁 astype(int64)//1e9（见 docs/learnings/）

## 任务1：扩充因子库的成交量族（判断层最大盲区，28维里量类只占3）
在 src/factors/library.py 追加 3 个因果、无前视的量价因子：
- obv_slope: 20根 OBV 的斜率（吸筹/派发），OBV=累计(sign(close.diff())*volume)
- vol_dryup: 密集期间量 / 前48根均量（VSA：越枯越猛）
- taker_imbalance: 若数据有 taker buy 量则用，否则用 (close-low)/(high-low)*volume 的20根均值近似买压
参照现有 alpha_* 写法（每个都要 docstring 写明用的列和窗口）。
然后跑 scripts/factor_ic_screen.py，看这3个量因子 IC/分类，写进 analysis/p2b_factor_ic_report.md 增补节。
判定：任一量因子 |IC|≥0.03 且符号稳定 → 标记为候选，报告注明"待owner单变量验证"。

## 任务2：H3 结构出场实验（研究议程）
labeling.py 加新函数 label_candidate_ma_exit：入场后收盘价跌破 EMA21 即出场（其余同TP5/SL2的entry/atr规则）。
不改现有 label 函数。用 barrier_sweep 或独立脚本，在 SWAP 池对比 ma_exit vs TP5/SL2 基线：
val AUC、p、top-decile 净@maker、平均持仓时长。写 analysis/p15_h3_ma_exit_report.md。
判定：净@maker ≥ TP5基线 且 p<0.01 → 发现级通过（不切主线，记录待前向）。

## 任务3：代码卫生（低风险，规格明确）
- 合并重复的隧道脚本：scripts/tunnel_labelstudio.sh 与 scripts/ls_reverse_tunnel.sh 功能重复，保留前者，后者改为调用前者或删除（确认无其他引用）
- 把 queue8/9/10/12/14/15 里手写的 F1 评估循环，替换为 from src.detection.owner_eval import evaluate_owner_f1（该模块已存在）
- 不改任何行为，只去重；跑 python3 -m pytest 确认测试仍绿

## 交付
每个任务：代码 + 报告 + 独立 commit + push grok/overnight 分支。
全部完成后在 grok_tasks/RESULTS.md 写一页总结（每任务一段：做了什么、结论、有无异常）。
