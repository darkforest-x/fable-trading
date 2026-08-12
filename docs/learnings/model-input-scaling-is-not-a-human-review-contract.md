# 模型输入缩放合同不能直接当成人工审核合同

- **问题**：Local Signal V2 的 Owner 语义审核图直接复用了 YOLO 输入 PNG。渲染器为训练稳定性强制价格轴至少覆盖现价的 6%，真实波动较小时，K 线在人眼看来被压成一条水平带，Owner 无法可靠判断形态。
- **死胡同**：继续放大同一张 PNG、增加页面宽度或把未来 K 线拼到同一纵轴都无效；前两者不会恢复已经被 6% 轴压缩的纵向信息，后一种还会被后续大行情进一步压扁，并混淆模型输入与人工证据。
- **有效路径**：保留逐字节一致的模型输入作为独立 lineage 产物；另用真实可见 OHLC 与 SMA/EMA 的实际价差为人工审核图建立自适应纵轴。因果图和未来对照分别缩放、分别存图，候选框按真实 bar 与高低价重新映射。未来对照只作人工参考，显式标记为不可训练，并在 holdout 边界前截断。
- **通用规则**：遇到“图看起来平、挤或空”时，第一步比较真实波幅与 renderer 的最小轴跨度；模型像素合同和人类可读合同必须物理分离，不能靠同一张图兼任。
- **牵连**：`yoyo.layers.l1_detection.render.MIN_REL_SPAN=0.06`、`scripts/build_local_signal_v2_semantic_review.py`、`scripts/build_owner_short_hardneg_canary_review.py`、holdout 起点 `2026-05-04T00:00:00Z`。
