# 候选池扩容必须继承旧池的配额状态

- **问题**：从已交付的 1,000 个候选再追加 9,000 个时，只在新批次内部去重和限额，会让同一事件重复入池，也会让某个币或某一天在合并后的 10,000 池中突破原来的最大占比。
- **死胡同**：把旧 manifest 只当作事后查重表，或简单沿用“每边每币最多 8 个”，都不对。前者没有在选择过程中占用额度；后者把绝对数冻结在小池规模，改变了扩容后的抽样分布，而把上限直接放大又容易忘记旧 500 条已经占用的额度。
- **有效路径**：先哈希锁定旧 manifest，用 `(symbol, direction, anchor_time)` 校验身份唯一性；选择新增样本前，把旧行写入同币同向 224 根排斥区间、币种计数和 UTC 日计数。配额按旧池最大份额等比例换算：`8/500 = 80/5000 = 1.6%`，最后对旧+新联合池重跑间隔与配额审计。
- **通用规则**：任何“追加 N 个”的候选/训练池任务，第一步先把所有已交付批次作为不可变初始状态注入选择器；不能只在新批次内满足约束后再拼接。
- **牵连**：`experiments/active/exp-15m-ma-launch-candidate1000-v1/results/review_manifest.jsonl`、`experiments/active/exp-15m-ma-launch-candidate9000-v1/preregistration.json`、`yoyo/datasets/fifteen_minute_launch_candidates.py`、同币同向 224 根间隔、每币/每 UTC 日 1.6% 最大份额。
