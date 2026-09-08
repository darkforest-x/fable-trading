# 可见 IMACD 释放标记不等于已经验证均线密集启动

- **问题**：2026-09-08，Owner 用 ETH 4H、SOPH 30m、XAUUSDT.P 4H、ZEC 4H 四张截图说明希望减少噪音、保留真正启动。当前通知忠实跟随可见 `focusRelease`，但这只是事件一致性，不能证明形成结构或未来延续质量。
- **死胡同**：本轮没有运行过滤实验。直接增加近零根数、要求更多均线交叉或突破整个整理框，都是未经验证的捷径：根数不能替代结构；紧而平行的均线不一定频繁交叉；截图中 ZEC 的信号收盘 506.26 仍低于整段整理框上沿，箱顶门会改变该示例的触发时点。截图中的后续上涨不能倒灌为信号当时的特征。
- **有效路径**：先核对事件公式和通知协议。Pine V2.4 的 `focusRelease` 只要求已合格近零段后主线越过冻结阈值；monitor 明确设置 `dense_filters_notifications=False`、`htf_filters_notifications=False`。精确 md=0 只说明零滞后价格均线在 high/low 的 SMMA 通道内，并不约束六条 SMA/EMA 20/60/120 的相对位置。由此将待研究问题拆成“此前形成的结构、当根的方向脱离、之后的延续结果”，而非把箭头直接叫作优质趋势。本轮仅澄清语义，未证明任何新过滤器有效。
- **通用规则**：先检查触发事件实际上断言了什么，再增加筛选。形态应在释放之前确认并保留状态；启动当根可检验脱离，但不能反写形成区。分别验证遗漏优质形态和保留噪音，不能只报告信号数量下降。多个均线或周期同向仍是相关信息，不自动等于独立确认。
- **牵连**：`yoyo/evaluation/pine/imacd_dense_mtf_v2_4.pine`、`yoyo/monitor/signals.py`；参考 [密集启动与首次穿线的区别](dense-launch-is-not-the-first-cross-of-the-full-ma-bundle.md)、[机械密集与人工判断的历史偏差](owner-eye-is-anticorrelated-with-the-mechanical-dense-definition.md)。本轮没有读取额外行情、评估收益、变更阈值、修改 Pine/监控或发送通知；四图都是上涨示例，不能代表完整样本或已验证空头镜像。
