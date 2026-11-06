# 价格绘图验收必须包含拖动价格轴

- **问题**：月首周 V1.1 的数字正确，但 Owner 缩放蜡烛后线条停在另一个价位。原生菜单显示“当前无坐标”，拖动右轴可复现；此前静态截图没有覆盖这个状态。
- **死胡同**：反复比对源高低点或改变时区都无法修复价格轴绑定。`overlay=true` 说明面板位置，不能仅凭它断言已存在实例的坐标状态正确；显式加 `scale.right` 还可能新建独立轴。
- **有效路径**：先读实例菜单证实绑定状态，再对 line、box、label 全部设置 `force_overlay=true`，使价格对象使用主图坐标。新私有 V1.2 菜单为右侧；纵轴缩小、放回时对象与蜡烛共同变化，四条线宽统一为1。
- **通用规则**：价格正确、时间对齐、纵轴绑定是三项独立验收。对叠加指标，至少做一次实际拖动价格轴的前后对照；不要只查声明或静态图。
- **牵连**：`yoyo/evaluation/pine/month_first_week_1618_v1_2.pine`；https://www.tradingview.com/pine-script-docs/language/declaration-statements/ 和 https://www.tradingview.com/support/solutions/43000775579-indicator-moves-separately-or-is-not-attached-to-the-price/ 。纽约窗口、价格和原始历史版本保留。
