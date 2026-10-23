# Pine 报警条件与全市场覆盖是两个独立能力

- **问题**：为 V2.37 加入周一上下 1.618 触价报警，同时要求 TradingView 覆盖所有币种。
- **死胡同**：仅增加 `alertcondition()` 并创建当前图表报警，不会扫描其他币种；不能把指标代码完成当作全市场报警已经启用。官方列表报警入口在本机明确显示当前 Essential 方案需要升级 Premium，普通图表报警的商品下拉也只有当前币。
- **有效路径**：先在真实账户核实列表报警资格，再保留旧版、创建 V2.38 独立私有脚本。条件复用现有周一区间及其时区，以盘中高低价检测等于或越过，历史计算消耗每周各侧首次状态，显示开关不参与触发。报警使用“每根K线一次”，由服务器去重盘中更新；覆盖范围由实际自选列表决定。
- **通用规则**：分别核验价格公式、触发频率、每币状态、列表覆盖和账户权限。关闭图表或切币不等于修改已创建报警的快照。套餐阻塞时交付并验证可用代码，明确尚未启用的覆盖，不能擅自购买订阅。
- **牵连**：`yoyo/evaluation/pine/monday_range_v2_38.pine`；`tests/evaluation/monday_range_v2_38_native_checks.pine`；[TradingView 列表报警](https://www.tradingview.com/support/solutions/43000739708-watchlist-alerts-your-trading-edge/)；[Pine 报警文档](https://www.tradingview.com/pine-script-docs/concepts/alerts/)。报警周期建议 15m，以对齐纽约周一日界；不改原指标跨周期区间语义。
