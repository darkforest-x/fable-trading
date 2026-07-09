# 多日无人值守要预留磁盘余量

- **问题**：Mac 数据盘 97% 满时 YOLO 长训/checkpoint/日志仍在写，存在中途 ENOSPC 风险。
- **死胡同**：一边训一边盲目 `rm -rf` runs 或 dataset。
- **有效路径**：优先清 **可再下载缓存**（pip/Homebrew/go-build、未使用的 docker 镜像）；保留 active train `best.pt` 与 datasets；把 free 提到 ≥15–20GB 再继续重 IO。
- **通用规则**：长任务启动前看 `df`；清理顺序 = 可重建缓存 → 旧 smoke 产物 → 永远最后才动主数据集。
- **牵连**：E2.1 train `dense_15m_full_s_e21`、FO/LS、fetch resume。
