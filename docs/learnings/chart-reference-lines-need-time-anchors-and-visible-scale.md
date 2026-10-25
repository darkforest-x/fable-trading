# Reference-line correctness includes both time anchors and the initial price scale

- **问题**：巡检图表返回了正确的周一与 1.618 价格，但默认按形态小范围缩放，参考线留在屏幕外；全宽水平线也把周一高低点之前的历史涂成已知区间。
- **死胡同**：只检验四个价格或存在四条线，无法证明用户能看见它们。用预览窗口的最高、最低价作锚点会把近期行情当成周一区间；从周一零点统一起画也不等于从实际极值起画。
- **有效路径**：沿用纽约周一日历和原 1.618 公式，读取完整、已确认的周一 5 分钟数据，取极值首次出现的 K 线时间。上沿和周一高从高点起画，下沿和周一低从低点起画，到下周截止。初次预览纳入四条参考价，另提供形态细节视图。线上同时画名称和价格。Lightweight Charts 4.2 对小数 logical index 的坐标会截断，因此跨周期锚点用相邻整数坐标插值。缺少锚点时不画占位线，延迟响应必须通过币种、周和图表实例身份检查。
- **通用规则**：价格参考图的验收必须覆盖值、时间起点、时间终点、默认可见性与缩放/平移后的行为；数据存在、API 成功、DOM 含有价格都不能代替真实画面。
- **牵连**：`yoyo/monitor/monday_chart_levels.py`、`static/range-chart.js`、`static/yolo-chart-levels.js`、`static/monday-fib-chart.js`。对应测试覆盖完整周、重复极值、缺样、分数坐标和过期响应。实图证据位于 `output/playwright/patrol-compact/`；Stitch 来源是 `docs/design/stitch-20261025-compact-charts/source.json`。
