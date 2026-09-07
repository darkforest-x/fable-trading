# 统一时区并不等于统一时间整数单位

- **问题**：V35 同一个合法 5 分钟时间序列，换成 pandas datetime64 秒、毫秒或微秒表示后，被错误拒绝为不在格点。
- **死胡同**：认为 pd.to_datetime(..., utc=True) 后 .astype('int64') 就必然是纳秒；实际上带时区数组仍可保留原来的 resolution，拿微秒整数对纳秒 STEP.value 求余会错。
- **有效路径**：先统一 UTC，再显式 .dt.as_unit('ns')，最后判整格。用 s/ms/us 三种相同时间值的表示不变性反例验证，不能只测试默认 ns 构造器。
- **通用规则**：时间戳比较与时间戳整数运算是两层契约；把 datetime 转整数前，先显式确认 epoch 单位。
- **牵连**：yoyo/data/k1k2_genuine_flow_alignment.py；tests/test_k1k2_genuine_flow_alignment.py。问题在合成测试阶段发现，真实事件尚未载入。
