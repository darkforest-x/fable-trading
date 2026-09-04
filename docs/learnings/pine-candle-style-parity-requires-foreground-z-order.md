# Pine 蜡烛样式一致性还取决于前景层级

- **问题**：自定义 `plotcandle` 已把实体、影线和边框指向同一个动态颜色，但移动端仍混出原生红绿蜡烛，看起来像色值或 `wickcolor` 配错。
- **死胡同**：反复调整颜色常量、`barcolor`、`wickcolor` 和 `bordercolor`。这些参数只控制自定义绘图；若指标默认位于主图后方，前景里的原生 K 线仍能覆盖它们。
- **有效路径**：把指标声明为 `behind_chart=false`，同时保留同色 `plotcandle` 覆盖实体、影线与边框，使完整自定义蜡烛位于原生图表前方。问题的关键变量是 z-order，不是色值。
- **通用规则**：验收 Pine 蜡烛样式时必须分别检查颜色逻辑和绘制层级；看到原生红绿“漏出”时，先验证前景/后景与 `plotcandle` 覆盖关系，再改颜色。
- **牵连**：`indicator(..., behind_chart=false)`、`plotcandle(color/wickcolor/bordercolor)`、原生图表蜡烛样式、移动端渲染顺序。
