# Ultralytics 的长路径列表可能先整体预分配而不是按 batch 流式读取

- **问题**：对 1,470 张图片调用 `model.predict(source=[...], batch=16, stream=True)` 时，MPS 在第一张结果前报 `Invalid buffer size: 9.08 GiB`。
- **死胡同**：以为 `batch=16` 和 `stream=True` 会约束输入内存；实际上长 Python 路径列表先被解释为一个内存 source，预处理发生在内部 batch 迭代器之前，所以两个参数都没有阻止巨型缓冲区。
- **有效路径**：在调用 Ultralytics 之前由外层代码把路径显式切成 16 张一组，每组单独 `model.predict(..., stream=False)`，并逐组核对返回数量；同一 MPS 环境随后稳定完成 2,939 张图。
- **通用规则**：批量离线推理不要把成千上万个路径一次性作为 `source` 列表传入；先在调用边界外分块，并把“每块返回数 == 每块输入数”作为 fail-closed 守恒检查。
- **牵连**：`scripts/evaluate_15m_ma_launch_t3_hard_val.py`、Ultralytics 8.4.89、Torch 2.8.0、MPS、`batch=16`、`imgsz=960`。
