# images 目录同时容纳实验输入、历史证据与可重建缓存

- **问题**：2026-09-22 磁盘清理审计发现，排除虚拟环境和依赖包、且不跟随目录软链接后，仓库有 154 个最外层 `images`/`imgs` 目录，分配空间合计约 24.16 GB；其中 `datasets` 约 18.38 GB。名字相同不代表生命周期相同。
- **死胡同**：不能把目录中的所有文件都当作预览图，也不能把旧工作目录全部当作重复副本。`datasets/eth_3m_short_pilot_v1/images` 的约 0.54 GB 中，约 0.522 GB 是 `.npy` 缓存，PNG 仅约 0.019 GB。旧工作目录的 22,570 张图片中，58 张在主目录对应路径不存在；“整棵目录都可安全删除”不成立。本轮没有删除图片。
- **有效路径**：先按文件扩展名统计，再核对生成/读取代码、对应原图和内容哈希。183 个 `.npy` 均有同名 PNG；本机 `.venv/lib/python3.9/site-packages/ultralytics/data/base.py:229` 以后缺少 NPY 时回读原图，`:296` 的 `cache_images_to_disk` 写入 NPY。旧目录 `.claude/worktrees/fable-trading-consolidation-758d61` 中 22,512 张图片与主目录同相对路径的 SHA-256 一致，分配空间约 2.666 GB；另 58 张约 36.11 MB 未匹配。只有前者可列为保留主副本、清理旧副本的候选，不能外推到旧目录内代码或其他文件。
- **通用规则**：清理优先级是“确认可回读原图的缓存 → 哈希确认的冗余副本 → owner 决定淘汰的历史实验图片”。保留 manifest、标签、原始金标和失败实验记录。重复图片即使主副本还在，删除旧路径也会影响旧工作目录内的直接路径读取；不能把内容重复说成完全无副作用。
- **牵连**：`scripts/windows/train_ma_profit3r.py:162` 的像素/标签校验直接读取 manifest 图片；`datasets/ma_profit3r_owner1500_neg_v5/DO_NOT_TRAIN_LABEL_SEMANTICS.json` 记录因标签语义拒绝训练及保留现有文件的历史决定。`experiments/active/exp-ma-morphology-negatives-20260922-v3/pilot20/summary.json:85` 起引用的部分 1m CSV 已在本轮按 owner 要求删除，因此其图片不能声称当前可直接本地再生。仅对照文件内容和分配块，未测量 APFS 克隆共享块或实际清理回收量；单位为十进制 GB。
