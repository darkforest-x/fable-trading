# 小型掩码矩阵告警要换成可审计归约而不是静默屏蔽

- **问题**：因果分段的置换检验只包含有限收益与非零分组计数，NumPy 1.26 在当前 macOS Accelerate 后端执行小型掩码矩阵乘法时仍发出 divide/overflow/invalid 警告；结果虽为有限数，也不能在告警未解释时用于选型。
- **死胡同**：看到最终数组有限就接受结果，或用 `np.errstate` / warning filter 隐藏告警。这会让真实的零计数、非有限收益与后端伪告警无法区分，破坏评估收据的可信度。
- **有效路径**：先逐项断言收益有限、每个半年分组计数大于预注册下限，再用 `np.einsum(..., optimize=False)` 做显式掩码归约，避开异常后端路径；另加一个直接逐组均值公式的单测，核对观测最大统计量完全一致。
- **通用规则**：评估代码出现浮点告警时，第一步验证输入域与分母，再替换为语义透明的归约并与朴素公式交叉验证；不要把“输出看起来正常”当作数值正确证据。
- **牵连**：`scripts/research_btcusdtp_k1k2_causal_failure_map.py` 的 familywise max-statistic permutation、macOS Accelerate、诊断 selection receipt；不改变策略规则或数据窗口。
