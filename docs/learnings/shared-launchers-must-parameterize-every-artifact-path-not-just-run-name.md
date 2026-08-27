# 共享训练启动器必须参数化整条产物链，而不只是运行名

- **问题**：启动器表面上允许覆盖 experiment、prereg、run name 和 imgsz，但 dataset、build/QA receipt、fetch 目录仍写死旧实验；新数据集即使哈希正确，也会被旧 QA 门检查或取回到错误目录。
- **死胡同**：只再写一个 wrapper 覆盖已有变量。这样命令看起来指向新实验，真正的本地 gate 与 fetch 仍偷偷依赖旧路径，最危险的结果不是立即报错，而是把旧回执当成新数据证据。
- **有效路径**：把 dataset、build receipt、QA receipt、local output root 和审计图数同时提升为显式环境合同；所有 gate 只读已解析出的变量。wrapper 只选择一组完整合同，共享启动器继续负责哈希、远端 staging sentinel 和禁止覆盖。
- **通用规则**：复用训练启动器前，沿着 `输入 → QA gate → 上传 → 远端路径 → fetch` 逐段搜索旧实验名；任何一段仍硬编码，就不算可复用启动器。
- **牵连**：`scripts/train_15m_ma_launch_t3_on_3060.sh`、`scripts/train_15m_ma_launch_owner_neg30000_on_3060.sh`、preregistration immutable inputs、远端 dataset 名、训练结果取回目录；主机 IP 漂移另见 [3060-lan-ip-can-drift-from-dot5.md](3060-lan-ip-can-drift-from-dot5.md)。
