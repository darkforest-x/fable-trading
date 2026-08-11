# 动态重裁剪不能修复旧标签语义

- **问题**：旧W20–30图的红框高度集中在右侧。按`前文6–10 + 核心5/7 + 后文3–5`重新渲染后，
  红框位置已自然分散，但目视仍发现部分框把启动大K包入“平台核心”。
- **死胡同**：把位置分布修复当成标签修复，认为窗口不再固定最右就可以直接训练。这样只消除了
  coordinate shortcut，却保留了错误的类别/边界监督，模型仍会把启动结果当成平台语义。
- **有效路径**：把校准拆成两道门。第一道只验上下文长度、后文延迟和位置分布；第二道独立由Owner
  同时裁决“是不是目标形态”和“核心边界在哪里”。第一道即使全绿，样本仍保持
  `training_eligible=false`，直到第二道完成。
- **通用规则**：重采样、重裁剪、重渲染只能改变输入分布，不能改变监督真值。任何旧框迁移到新
  语义时，先把它降级为proposal，再分别验类别纯度与几何边界。
- **牵连**：`scripts/build_owner_eth_shortdelay_calibration.py`；
  `analysis/output/owner_eth_shortdelay_calibration30_v1/`；
  `datasets/dense_owner_w20_midbox/w20_manifest.json`；
  `datasets/local_signal_v2_stagea_randomcrop_v1/w20_manifest.json`；后续Owner裁决与正式短窗训练集。
