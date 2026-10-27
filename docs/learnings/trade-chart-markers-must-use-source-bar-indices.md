# 逐单图必须用成交索引定位 K 线，不能直接拿账本时间找下一根

- **问题**：导出 v3lock / spike 的逐单图时，原生账本的 `entry_time` 比源行情 `frame.index[entry_i]` 晚 5 分钟。
- **死胡同**：直接断言时间与源索引一致，ATUSDT 2026-07-14 的预览失败；按时间直接搜索虽能画图，却会把箭头放到下一根 K 线上。
- **有效路径**：读取原适配器的时间契约。输入框架前索引加一个执行周期；普通成交保留 close 时间，跳空止损另外记逻辑 open 时间。图上按 `entry_i` / `exit_i` 定位源 K 线，核对入场为源 open + 5m，退出为源 open 或 open + 5m，单独展示原账本时间。
- **通用规则**：画回测成交图先核对 bar open、bar close、原生事件时间与逻辑成交时间；有原始索引就优先使用，不以时间字符串猜测盘中先后。
- **牵连**：`yoyo/layers/l3_backtest/nautilus_independent.py`、`yoyo/evaluation/paper_2026_trade_charts.py`；原结果和成交语义不变，只校准图像定位。
