# 因果位置多样性需要空白画布槽位

- **问题**：Stage-B 窗口右端固定为 decision bar，anchor 只早 1–2 根；即使窗口长度在 20–30 根变化，标签仍集中在最右侧约 89%–95%，固定 30 根时更只剩 93.1%/94.8% 两个位置。模型因此可能把“最右侧有局部波动”学成 shortcut。
- **死胡同**：缩短或随机窗口长度不能把 anchor 扩展到要求的 65%–95%；把窗口右端移到 decision 之后虽能改变位置，却会把未来真实 K 线画进图里，直接破坏因果性。
- **有效路径**：把“可见历史 bar 数”和“画布横向槽位数”分离：输入仍只包含 decision 及以前的真实 K 线，但渲染时在右侧保留随机数量的纯空白槽位，标签和负例共用同一槽位变换。这样移动的是布局，不是信息边界。
- **通用规则**：审计图像位置多样性时必须同时检查真实可见末端和画布坐标分布；`future_bars=0` 只证明无前视，不证明没有位置 shortcut。
- **牵连**：`scripts/build_local_signal_v2_stageb.py`、`yoyo.layers.l1_detection.local_v2_render`（旧 `render.py` 像素合同不改）、正负样本一致的 padding 分布、live renderer 契约、P1/P2 预注册与 event-level 评估。
