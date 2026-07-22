# YOLO epoch-end val 可在 ap_per_class 把 16GB 打爆

- **问题**：3060（16GB RAM）上 tipval 训完 epoch1 后，val 推理条跑满，随后在
  `ultralytics.utils.metrics.ap_per_class` 的 `cumsum` 抛 `numpy MemoryError`
  （连 1.2MiB 都分不出）——典型「RAM 已耗尽/碎片化」而非真缺那一页。
- **死胡同**：只盯 VRAM / batch；`plots=False` 和 `save_json=False` 早已开着仍挂——
  瓶颈是 workers 占主机 RAM + 全量 val 预测缓冲，不是画图。
- **有效路径**：同一 tipval 数据语义下：`workers 4→2`、`batch 16→8`，并 `max_det=100`
  压低 metric 缓冲；新 run 名 `owner_v15_tipval_oomfix`。epoch1 val 出 mAP 后进 ep2。
- **通用规则**：16GB 箱上 YOLO 挂在「Class … mAP50 100%」之后 → 先降 workers/batch，
  再考虑 `max_det`；别先改标签或 val 语义。
- **牵连**：远端 `C:/fable/train_dense.py`；`scripts/v15_train_start.sh` /
  `v15_train_status.sh`；日志 `C:\fable\logs\owner_v15_tipval_oomfix.log`
