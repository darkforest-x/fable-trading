# 确定性训练需要参数契约与运行时指纹同时成立

- **问题**：L2 的训练虽然固定了总 seed，但 LightGBM 仍可能因直方图构建模式、其他独立随机源、线程调度或依赖版本差异而产生不同结果；只记录数据集和脚本哈希不足以复现最终分数与阈值。
- **死胡同**：把 `seed=42` 或 `deterministic=true` 单独当作复现保证。前者没有显式覆盖 LightGBM 的各个随机源，后者若不同时固定 `force_col_wise` / `force_row_wise`，官方文档仍提示可能出现数值不稳定；两者也都不能解释跨版本、跨编译器或跨平台的差异。
- **有效路径**：在预注册中同时冻结 CPU、`deterministic=true`、`force_col_wise=true`、`num_threads=1` 以及 data / feature_fraction / bagging / extra 四个 seed；把训练器文件纳入不可变输入哈希；训练时把实际生效参数和 Python、平台、LightGBM、NumPy、pandas、scikit-learn、SciPy 版本写入回执，并在启动前拒绝核心运行时版本漂移。
- **通用规则**：任何需要与历史曲线逐数对照的机器学习实验，第一步都要把“输入哈希 + 生效参数 + 随机源 + 算法运行时 + 平台指纹”作为一个整体契约；缺少其中任一轴，只能说配置相似，不能说结果可复现。
- **牵连**：`experiments/active/exp-15m-ma-launch-l2-global-context-v1/preregistration.json`、`scripts/research_15m_ma_launch_l2_global_context.py`、`yoyo/layers/l2_judgment/train.py`、LightGBM 4.6.0 [deterministic 参数说明](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html#deterministic)。
