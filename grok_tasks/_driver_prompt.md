你在 fable-trading 仓库、当前已切到 grok/overnight 分支，无监督自主运行整晚。
读并严格执行 grok_tasks/overnight_batch.md 的 10 个任务（含顶部铁律节，你无权修改铁律）。
按 1→10 顺序做，每个任务：写代码/脚本 → 跑 → 写报告 → git add + commit + push origin grok/overnight。
某任务卡住超过合理时间就跳过，在 grok_tasks/RESULTS.md 记录原因，继续下一个，绝不在单任务上耗整晚。
全部做完在 grok_tasks/RESULTS.md 写总结（每任务一段：做了什么/结论/异常）。
关键约束：禁改 features.py 主线特征表、禁碰 frozen 配置和 data/forward_log.csv、禁评估 holdout、只用 train/val、负结果照样入库。
