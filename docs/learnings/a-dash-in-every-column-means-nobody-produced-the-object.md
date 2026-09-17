# 每一列都是破折号，说明没人生产那个对象，不是前端没接好

- **问题**：Owner 报「V9 前端是不是没接好」——看板统计区的运行中/已结束/R/胜率全是 `—`。
  第一反应是去查前端渲染、API 过滤、缓存。
- **死胡同**：查 `/api/signals` 返回 0 条——其实是我自己的 jq 式取值写错了运算符优先级
  （`d.get('items') or d if isinstance(d,list) else []` 整体走了 else 分支）。
  差点据此判定 API 坏了。用浏览器实打开看板才发现卡片渲染完全正常，只有 R 那几列是破折号。
- **有效路径**：区分「某些值缺失」和「全部值缺失」。零星缺失才是过滤/缓存问题；
  **整列无一例外**指向上游根本没产出这个字段。顺着前端读的字段名
  （`performance.status / current_r`）反查后端，发现 `v9_signals.analyze` 写的是字符串
  `"performance": "not_tracked"`，而 `signal_analytics.performance()` 只接受 dict，
  于是每张卡片都稳定落进「仅入场参考」分支。前端早就写好了，只是没人喂它。
- **通用规则**：看到**整列**统一的空值/默认值，先去生产端搜那个字段名，别先查展示端。
  另外：用浏览器真打开页面看一眼，比连写三个 curl 快——排查显示问题时，
  自己写的解析脚本和被查的系统一样可能有 bug。
- **牵连**：`yoyo/monitor/v9_signals.py`、`yoyo/monitor/signal_analytics.py`、
  `yoyo/monitor/static/app.js:332`（`performanceView`）。
  同一轮还发现「Bark · 异常」徽标只看历史累计 `unknown>0`，两天前的 2 条不确定发送
  会把徽标永久钉红——同样是「显示逻辑没反映当前状态」，见 app.js:616。
