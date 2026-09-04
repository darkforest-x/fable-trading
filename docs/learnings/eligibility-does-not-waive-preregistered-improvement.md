# 样本门槛不能豁免改善门槛

- **问题**：参数搜索的 incumbent 未满足最低样本数时，选择器把第一个“样本够多”的候选直接当成新 incumbent，绕过了预注册的 `+2bp` 稳健改善要求，结果选中了收益反而更差的参数。
- **死胡同**：把“候选可评估”与“候选优于当前参数”合并成一个布尔条件。资格门只说明统计量可以比较，不说明应当接受；incumbent 不合格也不意味着任意合格候选都有证据胜出。
- **有效路径**：把选择规则拆成独立的硬门：候选必须先满足总样本与逐折样本数，再同时满足相对当前配置的稳健均值改善、最差折退化上限和预注册 tie-break；没有候选同时过门时明确保持 inherited 参数，并在冻结验证前提交选择收据。
- **通用规则**：顺序式参数优化必须始终相对“当前实际参数”计算改善，incumbent 是否达到最终部署资格不能改变比较基准；任何 waiver 都必须在运行前预注册，运行后补出的豁免一律无效。
- **牵连**：`scripts/optimize_btcusdtp_k1k2_intraday.py`、`tests/test_optimize_btcusdtp_k1k2_intraday.py`、`experiments/active/exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v2/results/invalid_run01/`、`development_selection_receipt.json`。
