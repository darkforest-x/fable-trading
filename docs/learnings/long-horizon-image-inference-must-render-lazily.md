# 长区间图像推理必须按批次惰性渲染

- **问题**：把全币种 4h 扫描从 6 个端点扩到 90 个端点后，任务数变成 49,064。若沿用“先把
  全部图渲染进列表、再交给模型”的短扫描实现，仅原始 1280×742×3 像素就需要约 140GB，实验
  会先死于内存，而不是模型推理。
- **死胡同**：只调小 YOLO `batch_size` 没用；batch 只控制模型一次吃多少张，预构建的 Python
  任务列表仍然持有全部图像。按币分块虽能降内存，但会改变原有跨币 batch 边界，给逐像素/逐框
  复现增加不必要的自由度。
- **有效路径**：先只冻结轻量的 `(symbol, start, end, window)` 规格，让一个实现 `Sequence`
  的惰性任务容器在推理器请求切片时才渲染该 batch。这样保持原 symbol→endpoint→W18/W19 顺序
  和原 batch 边界，常驻内存从百 GB 级降到单 batch 级；用 monkeypatch 回归测试证明
  `build_tasks()` 阶段没有发生任何渲染。
- **通用规则**：时间跨度或资产数扩大前，先用 `任务数 × 单样本像素字节` 做内存预算；只要完整
  物化超过可用内存，就在数据/模型合同不变的前提下改为惰性切片或流式批处理，而不是靠缩小模型
  batch 假装解决。
- **牵连**：`scripts/scan_4h_ma_launch_yolo_latest.py` 的 `LazyTaskSequence` / `build_tasks()`、
  `scripts/scan_4h_ma_launch_yolo_half_month.py` 的 90 端点实验、
  `tests/test_scan_4h_ma_launch_yolo_latest.py` 的零预渲染回归测试；YOLO 权重、阈值、输入像素与
  事件合同均不改变。
