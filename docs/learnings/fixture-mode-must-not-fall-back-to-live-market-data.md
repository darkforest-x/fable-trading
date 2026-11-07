# 测试桩模式下的显示兜底不能退回真实行情

- **问题**：原生 2.1.0 合约页在模拟器连 `serve_iphone_ui_fixture.py`（合成价 ~100）时，K 线纵轴拉到 8 万，蜡烛被压成一条横线。
- **死胡同**：先以为是假交易所某根插针或入场/止损参考线把纵轴撑大——fixture 确实有一根 106/94 的合成插针，但量级对不上。数据本身各自都“合法”，单看哪一路都查不出错。
- **有效路径**：页面首次出现时连接还没建立，`CandleStore.page()` 拿不到中转，按网页版逻辑退回公共 OKX REST，取到真实 BTC（~7.8 万）；随后推流来自 fixture（~99），两路合进同一序列。修法两步：① 连接变化（token）进入图表重载键，换连接时清空重取；② 服务地址不是生产域名时禁止公共兜底，直接报“K线暂不可用”。
- **通用规则**：任何“取不到就换数据源”的兜底，先问它和主数据源是不是同一个世界。测试桩、模拟盘、回放和实盘数据绝不能在同一张图或同一次计算里混用；兜底要按环境门控，且换源时整段序列重置，不做合并。
- **牵连**：`yoyo/discipline_app/native/SpikeDiscipline/Core/CandleStore.swift`（`page(before:)`）、`Trade/TradeView.swift`（`onChange` 重载键）、`Fib/FibView.swift`（`chartKey` 已含 token）。同类风险：1.x `candleTransport.ts` 也有公共兜底，但网页构建用 `VITE_SPIKE_CANDLES_HTTP` 指向 fixture，因而没暴露。
