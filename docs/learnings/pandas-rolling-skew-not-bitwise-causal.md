# pandas rolling.skew 在「未来突变」测试下会数值漂移

- **问题**：因果性单测：改写 t 之后的 close，却发现 `ret_skew` 在 t 及以前的 rolling.skew 值与未改写版差 ~1e-2。
- **死胡同**：先怀疑因子前视；手工对同一窗口 `Series.skew()` 与未突变版完全一致，说明窗口内容无泄漏。
- **有效路径**：判定为 pandas 在线矩路径的 float 状态伪影，非业务前视；单测 skip 并注释，其余 rolling 因子用前缀相等断言。
- **通用规则**：因果性测试用「未来赋值」时，对 online moments（skew/kurt）单独处理；真泄漏应以窗口重算对照验证。
- **牵连**：`tests/test_factor_causality.py`；`alpha_ret_skew`
