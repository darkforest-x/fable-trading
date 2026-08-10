# 画布位置多样不等于真实 K 线内容位置多样

- **问题**：blank-only 数据的框中心覆盖 65%–95%，四个画布位置桶也全绿，但 Owner 目视仍发现每个框都贴着真实 K 线的最后几根；模型仍可学习“内容末端就是信号”。
- **死胡同**：在 decision 右侧添加纯白槽位会把整段 K 线连同框一起左移，只改变绝对像素 X，不改变框相对真实 candle sequence 的位置。只审 `box_pos_frac` 会把这种布局扰动误报成真正的位置随机化。
- **有效路径**：把 Stage A 与 Stage B 分开。Stage A 从原始连续 K 线改变 `crop_start_bar`，按冻结比例采样 anchor offset，并要求框右侧存在真实历史 K；Stage B 才保持严格因果 tip。审计同时记录 `anchor_x_ratio`、`real_bars_after_decision` 和 `right_blank_slots`。
- **通用规则**：任何位置增强先明确坐标系；至少分别检查“相对画布”和“相对真实内容”。若任务要求内容位置不变性，移动画布、letterbox、padding 都不是数据层随机裁剪的替代品。
- **牵连**：`scripts/build_local_signal_v2_stagea.py`、`scripts/audit_local_signal_v2_stagea.py`、`tests/test_local_signal_v2_stagea.py`、`causal_blank_w30_v3` owner reject、Stage A/Stage B 课程式训练边界。
