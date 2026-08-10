# 因果位置多样性需要空白画布槽位

> **2026-08-11 纠正**：本条只解决“相对整张画布的 X 不固定”，不能解决“框相对真实 K 线
> 内容仍固定在末端”。Owner 目视否决 blank-only 数据后，真正的 Stage A 位置多样性改为从
> 原始连续 K 线改变 `crop_start_bar`；详见
> [canvas-position-is-not-content-position.md](canvas-position-is-not-content-position.md)。

- **问题**：Stage-B 窗口右端固定为 decision bar，anchor 只早 1–2 根；即使窗口长度在 20–30 根变化，标签仍集中在最右侧约 89%–95%，固定 30 根时更只剩 93.1%/94.8% 两个位置。模型因此可能把“最右侧有局部波动”学成 shortcut。
- **死胡同**：缩短或随机窗口长度不能把 anchor 扩展到要求的 65%–95%；把窗口右端移到 decision 之后虽能改变位置，却会把未来真实 K 线画进图里，直接破坏因果性。
- **有效路径**：空白槽位只能作为 Stage B 的画布扰动；若目标是 Stage A 形态表征位置不变性，必须从原始连续 K 线改变裁剪起点并重新渲染真实窗口。
- **通用规则**：审计图像位置多样性时必须同时检查画布 X、相对真实 K 线的 anchor X、框到真实内容末端的 bar gap；`future_bars=0` 只证明无前视，不证明没有位置 shortcut。
- **牵连**：`scripts/build_local_signal_v2_stageb.py`、`yoyo.layers.l1_detection.local_v2_render`（旧 `render.py` 像素合同不改）、正负样本一致的 padding 分布、live renderer 契约、P1/P2 预注册与 event-level 评估。
