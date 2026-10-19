# 信号融合可以按区间相交，但必须服从较晚可用时间

- **问题**：Pine 均线交叉是单根 K 线事件，YOLO 检测却覆盖一个核心与确认窗口；要求两个时间戳
  完全相等会漏掉语义上属于同一次启动的匹配，直接使用较早时间又会把尚未完成的模型判断回填到过去。

- **死胡同**：用 `pine_time == detection_time` 做硬连接，或者为了提高重合率把模型框映射回核心
  起点并在那个历史时刻成交。前者把表示差异当成信号差异，后者则制造前视。

- **有效路径**：每个来源同时记录事件区间和 `available_at`。同币、同方向的两个闭区间只要相交
  就可视为同一候选，不要求落在同一根 bar；融合判断的可用时间固定为两个 `available_at` 的较晚者，
  成交只能发生在该时点之后的第一个合法开盘。

- **通用规则**：匹配口径可以宽松，执行时钟不能宽松。先判断事件区间是否相交，再计算
  `combined_available_at = max(left.available_at, right.available_at)`；任何命中率优化都不得改变这条式子。

- **牵连**：`scripts/run_pine_eth_15m_v12f.py` 的 signal ledger、未来 YOLO/Pine 融合器、
  [K 线开盘戳不能直接写成信号可用时间](bar-open-timestamps-must-not-be-labeled-as-signal-availability.md)、
  无前视纪律与 next-open 成交口径。
