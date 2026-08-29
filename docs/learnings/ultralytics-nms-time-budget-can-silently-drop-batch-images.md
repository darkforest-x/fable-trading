# Ultralytics NMS 超时会静默漏掉同批后续图片

- **问题**：长时间因果扫描打印 `NMS time limit 3.600s exceeded`。Ultralytics 的 NMS 超时分支不是只记录性能告警，而是直接 `break`，所以该批后续图片没有结果，最终检测密度会被系统性低估。
- **死胡同**：继续等待并相信最终 `scan_summary.json` 的 exposure 数不行；exposure 只表示图片送入了扫描循环，不能证明每张图片都完成 NMS。提高 batch 也不行，它同时放大 MPS 内存和批级 NMS 截断风险。把 NMS 搬到 CPU 但仍走纯 Torch 循环虽然完整，密集候选下又会慢一个数量级。
- **有效路径**：中止尚未产出最终 artifact 的无效运行；把 FP32 模型原始预测复制到 CPU，预加载 torchvision 的编译版 NMS，并显式把 `max_time_img` 从默认 0.05 提高到 60 秒。先在冻结 ETH 48 小时 1,536 窗口上复跑，要求 raw/event JSONL 逐字节一致后，才重启正式双模型扫描。
- **通用规则**：任何 Ultralytics 批量推理只要出现 NMS time-limit warning，该轮立即作废。验收不仅检查 exposure 总数，还要禁用批内截断，并用冻结小切片做 artifact-level parity。
- **牵连**：`scripts/backtest_owner_short_gold_center_recent.py` 的 `--nms-device` / `--nms-implementation` / `--nms-max-time-img`；`scripts/summarize_owner_short_recent_core10.py` 的运行完整性门；MPS、batch、conf、IoU 与 holdout 读取登记。
