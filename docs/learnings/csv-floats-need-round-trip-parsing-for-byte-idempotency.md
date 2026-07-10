# CSV 幂等必须保留浮点往返精度

- **问题**：forward 第二轮显示 `new_signals=0`、`closed_updates=0`，文件行数也不变，但 SHA 仍变化。
- **死胡同**：先怀疑 `detected_at` 被刷新或行序不稳定；merge 测试和第三轮 diff 都否定了这两种解释。
- **有效路径**：用含真实阈值精度的最小读写测试复现，确认 pandas 默认解析把末位浮点缩短；读取时启用 `float_precision="round_trip"` 后首轮与读回字节一致。
- **通用规则**：要求字节级幂等的 CSV 账本，测试必须覆盖“写入高精度浮点 → 读取 → 再写”的完整往返，不能只断言行数和业务键。
- **牵连**：`src/judgment/forward_records.py`、`tests/test_forward_tracking.py`、主线与所有影子 forward CSV。
