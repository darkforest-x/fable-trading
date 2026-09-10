# 复现命令

在 `/Users/zhangzc/fable-trading` 使用现有 `.venv`。先核对原研究的RESULTS_MANIFEST、已冻结原始源与feature SHA。正式输出目录不能含先前运行记录，已有attempt必须保留；后续复核使用单独目录并记录新增历史消耗，禁止覆盖后仍称首次运行。

```bash
.venv/bin/python -m pytest -q tests/test_launch_quality_dataset.py tests/test_launch_quality_statistics.py tests/test_launch_quality_accounts.py
.venv/bin/python -m yoyo.evaluation.launch_quality_dataset --old-experiment experiments/active/exp-altseason-multivenue-20260910-v1 --output experiments/active/exp-launch-quality-20260910-v1/results
.venv/bin/python -m yoyo.evaluation.launch_quality_accounts --results experiments/active/exp-launch-quality-20260910-v1/results
.venv/bin/python -m yoyo.evaluation.launch_quality_report --results experiments/active/exp-launch-quality-20260910-v1/results
```

先提交builder，再执行真实历史评分。dataset完整冻结earlier匹配索引后才计算收益；accounts读取同一配置结果并生成不同过滤账户，不搜索参数。原随机控制要求history_count>=340，而真实focus候选沿用ready（index>=340）；保留原有一根差异，不偷偷改变对照协议。所有earlier退出严格截到7月10日边界；known四所scope基线必须与原版收益、回撤、成交数一致。

报告可重复渲染已保存账本和原价格验证，策略评分不重复。图中展示的最优/最差/被过滤案例是解释样本，不作为独立统计证据。资金费和真实冲击尚未完整覆盖，所有产物training_eligible/production_eligible=false。
